import atexit
import contextlib
import os
from subprocess import CalledProcessError, check_output
from typing import Any

from pwnellij.models import Multiplexer, Split


def _decode(res: bytes | str, delimiter: str = ":") -> list[str]:
    with contextlib.suppress(AttributeError):
        res = res.decode("utf-8")
    return res.strip().split(delimiter)


class TmuxBackend:
    """Thin wrapper around the tmux binary and process environment.

    All tmux interaction routes through here so tests can substitute a fake.
    """

    def run(self, *args: str) -> bytes:
        return check_output(["tmux", *args])

    def in_tmux(self) -> bool:
        return "TMUX_PANE" in os.environ

    def current_pane(self) -> str:
        return os.environ["TMUX_PANE"]


class Tmux(Multiplexer):
    def __init__(
        self,
        cmd: str = "/bin/cat -",
        backend: TmuxBackend | None = None,
        mouse: bool = True,
    ) -> None:
        self.backend = backend or TmuxBackend()
        if not self.backend.in_tmux():
            raise RuntimeError(
                "pwnellij Tmux multiplexer requires a running tmux session "
                "(TMUX_PANE is not set in the environment)"
            )
        self.cmd = cmd
        self.panes = [Split(self.backend.current_pane(), None, "main", {})]
        self._saved_options = self._window_options()
        if not any(o.startswith("pane-border-status") for o in self._saved_options):
            self._saved_options.append("pane-border-status off")
        # Mouse mode lets the wheel scroll each pane's copy-mode history,
        # which is the only way to review output that scrolled out of a pane.
        # Remember the prior value so close() can put it back.
        self._saved_mouse: str | None = None
        if mouse:
            self._saved_mouse = self._option("mouse")
            self.backend.run("set", "mouse", "on")
        atexit.register(self.close)

    # --- internals -------------------------------------------------------

    def _window_options(self) -> list[str]:
        return _decode(self.backend.run("show-options", "-w"), delimiter="\n")

    def _option(self, name: str) -> str:
        return _decode(self.backend.run("show-options", "-v", name), delimiter="\n")[0]

    def _kill(self, pane_id: str) -> None:
        # Best effort: a failure means the pane (or the whole server) is
        # already gone, and close() runs at exit where any output would just
        # land in the user's shell.
        with contextlib.suppress(CalledProcessError):
            self.backend.run("kill-pane", "-t", pane_id)

    def _set_title(self, split: Split | None, title: str) -> None:
        if split is None:
            self.backend.run("select-pane", "-T", title)
        else:
            self.backend.run("select-pane", "-T", title, "-t", split.id)

    def _set_border_status(self, value: str) -> None:
        self.backend.run("set", "pane-border-status", value)

    def _do_split(
        self,
        *args: str,
        target: Split | None = None,
        display: str | None = None,
        cmd: str = "/bin/cat -",
        use_stdin: bool = False,
        size: str | None = None,
        **kwargs: Any,
    ) -> Split:
        cli = list(args)
        if target is not None:
            cli += ["-t", target.id]
        if size is not None:
            cli += ["-l", size]
        fd = "#{pane_tty}" if not use_stdin else "/proc/#{pane_pid}/fd/0"
        if use_stdin:
            cmd = "(cat)|" + cmd
        res = self.backend.run("split-window", "-P", "-d", "-F", "#{pane_id}:" + fd, *cli, cmd)
        return Split(*_decode(res), display, kwargs)

    # --- Multiplexer API -------------------------------------------------

    def get(self, display: str | Split) -> Split | None:
        if isinstance(display, Split):
            return display
        return next((p for p in self.panes if p.display == display), None)

    def show(
        self,
        display: str,
        on: str | Split | None = None,
        **kwargs: Any,
    ) -> Split:
        if isinstance(on, str):
            on = self.get(on)
        split = on._replace(display=display, settings=on.settings.copy())
        split.settings.update(kwargs)
        self.panes.append(split)
        if display:
            self._set_title(
                split,
                ", ".join(sp.display for sp in self.panes if sp.tty == split.tty),
            )
        return split

    def split(
        self,
        *args: str,
        target: str | Split | None = None,
        display: str | None = None,
        cmd: str | None = None,
        use_stdin: bool | None = None,
        **kwargs: Any,
    ) -> Split:
        if isinstance(target, str):
            target = self.get(target)
        split = self._do_split(
            *args,
            target=target,
            display=display,
            cmd=cmd or self.cmd,
            use_stdin=use_stdin,
            **kwargs,
        )
        if display:
            self._set_title(split, display)
        self.panes.append(split)
        return split

    def left(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split:
        return self.split("-hb", *args, target=of, display=display, **kwargs)

    def right(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split:
        return self.split("-h", *args, target=of, display=display, **kwargs)

    def above(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split:
        return self.split("-vb", *args, target=of, display=display, **kwargs)

    def below(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split:
        return self.split("-v", *args, target=of, display=display, **kwargs)

    def splits(self) -> list[Split]:
        return self.panes

    def size(self, split: Split) -> list[int]:
        res = self.backend.run(
            "display", "-p", "-F", "#{pane_width}:#{pane_height}", "-t", split.id
        )
        return [int(x) for x in _decode(res)]

    def finish(self, **kwargs: Any) -> None:
        # tmux <2.6 selects the new pane after splitting; later versions stay put.
        # Force consistent behaviour by reselecting the main pane.
        self.backend.run("select-pane", "-t", self.backend.current_pane())

    def do(
        self,
        show_titles: bool | str | None = None,
        set_title: str | None = None,
        target: str | Split | None = None,
    ) -> None:
        """Tells tmux to do something. All actions are skipped when their
        corresponding parameter is None.

        Parameters
        ----------
        show_titles : bool|str
            True or "top" → titles in top border; "bottom" → bottom border;
            False → hide titles.
        set_title : str
            Sets the title of ``target`` (or the current split if None).
        target : str|Split
            Display name or Split to act on.
        """
        if show_titles is not None:
            self._set_border_status({"bottom": "bottom", False: "off"}.get(show_titles, "top"))
        if set_title is not None:
            self._set_title(self.get(target), set_title)

    def close(self) -> None:
        for pane_id in {p.id for p in self.panes[1:]}:
            self._kill(pane_id)
        if self._saved_mouse is not None:
            if self._saved_mouse:
                self.backend.run("set", "mouse", self._saved_mouse)
            else:
                # Empty means mouse was never set on the session; `set mouse ""`
                # toggles it on, so unset to fall back to the inherited value.
                self.backend.run("set", "-u", "mouse")
            self._saved_mouse = None
        for option in (o for o in self._saved_options if o):
            self.backend.run("set", *option.split(" "))

import atexit
import contextlib
import os
import shutil
import tempfile
import time
import warnings
from subprocess import CalledProcessError, check_output
from typing import Any
from uuid import uuid4

from pwnellij.models import Multiplexer, Split

# Writes tty, $ZELLIJ_PANE_ID, and `stty size` to a temp file, then atomically
# moves it into place so the polling side never sees a half-written sentinel.
_HELPER = (
    '{ tty; echo "$ZELLIJ_PANE_ID"; stty size; } > "$1.tmp" 2>/dev/null; '
    'mv "$1.tmp" "$1" 2>/dev/null; exec '
)

# zellij's `new-pane` only implements Right and Down (Left/Up are accepted but
# silently treated as Right/Down), so Left/Up splits are emulated: split in the
# opposite direction, then swap the two panes with `move-pane --pane-id`.
_REAL_DIRECTION = {"Left": "Right", "Up": "Down"}

# The border the new pane shares with the pane it was split off, i.e. the one
# to move when honoring a requested size.
_SHARED_EDGE = {"Right": "left", "Left": "right", "Down": "up", "Up": "down"}

_FOCUS_MAX_CYCLES = 32
_RESIZE_MAX_STEPS = 30


def _strip_pane_prefix(raw: str) -> str:
    # $ZELLIJ_PANE_ID looks like "terminal_42"; zellij's --pane-id wants just "42".
    return raw.removeprefix("terminal_")


class ZellijBackend:
    """Thin wrapper around the zellij binary and process environment.

    All zellij interaction routes through here so tests can substitute a fake.
    """

    def run(self, *args: str) -> bytes:
        return check_output(["zellij", *args])

    def in_zellij(self) -> bool:
        return "ZELLIJ" in os.environ

    def current_pane(self) -> str:
        return _strip_pane_prefix(os.environ["ZELLIJ_PANE_ID"])

    def focused_pane(self) -> str | None:
        """Pane id of the currently focused pane, parsed from ``list-clients``."""
        out = self.run("action", "list-clients").decode("utf-8", errors="replace")
        for line in out.splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 2:
                return _strip_pane_prefix(fields[1])
        return None

    def main_tty(self) -> str | None:
        """TTY of the calling process (the pane gdb runs in), if it has one."""
        for fd in (0, 1, 2):
            with contextlib.suppress(OSError):
                return os.ttyname(fd)
        return None

    def tty_size(self, tty: str | None) -> list[int] | None:
        """Live ``[cols, rows]`` of a pane, measured through its tty.

        zellij keeps each pane's pty winsize up to date, so an ioctl on the tty
        reflects the current pane size — including resizes after the split.
        Returns None when the tty cannot be opened or measured.
        """
        if not tty:
            return None
        try:
            fd = os.open(tty, os.O_RDONLY | os.O_NOCTTY)
        except OSError:
            return None
        try:
            size = os.get_terminal_size(fd)
        except (OSError, ValueError):
            return None
        finally:
            os.close(fd)
        return [size.columns, size.lines]

    def read_sentinel(
        self,
        path: str,
        timeout: float = 2.0,
        interval: float = 0.05,
    ) -> tuple[str, str, list[int]]:
        """Poll ``path`` until the helper renames it into place, then read it.

        File contents are three lines: tty, ``$ZELLIJ_PANE_ID``, ``stty size``.
        Returns ``(tty, pane_id, [width, height])``. Raises :class:`TimeoutError`
        if the sentinel never appears.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if os.path.exists(path):
                break
            time.sleep(interval)
        else:
            raise TimeoutError(f"sentinel never populated: {path}")
        with open(path) as fh:
            lines = fh.read().splitlines()
        tty = lines[0].strip() if lines else ""
        pane_id = _strip_pane_prefix(lines[1].strip()) if len(lines) > 1 else ""
        rows, cols = 24, 80
        if len(lines) > 2:
            parts = lines[2].split()
            if len(parts) == 2:
                with contextlib.suppress(ValueError):
                    rows, cols = int(parts[0]), int(parts[1])
        return tty, pane_id, [cols, rows]


class Zellij(Multiplexer):
    """Zellij implementation of :class:`~pwnellij.models.Multiplexer`.

    Unlike tmux, zellij does not expose a pane's ``/dev/pts/N`` path via the
    CLI. Each new pane is spawned with a tiny helper that writes its tty and
    ``stty size`` to a sentinel file, which is then polled to discover both.
    Sizes are afterwards measured live through that tty, so later terminal
    resizes are reflected in ``size()``.

    zellij's ``new-pane`` cannot target a pane and only supports the Right and
    Down directions, so the rest is emulated:

    - targeting (``of=``): focus is cycled with ``focus-next-pane`` until
      ``list-clients`` reports the target pane, then the split happens there.
    - Left/Up: split Right/Down, then swap the two panes with
      ``move-pane --pane-id``.
    - ``size=``: the new pane is nudged toward the requested size with
      ``resize`` steps, re-measuring through the pane tty after each step.
      Best effort — zellij resizes in coarse increments.
    """

    def __init__(
        self,
        cmd: str = "/bin/cat -",
        backend: ZellijBackend | None = None,
        sentinel_timeout: float = 2.0,
    ) -> None:
        self.backend = backend or ZellijBackend()
        if not self.backend.in_zellij():
            raise RuntimeError(
                "pwnellij Zellij multiplexer requires a running zellij session "
                "(ZELLIJ is not set in the environment)"
            )
        self.cmd = cmd
        self.sentinel_timeout = sentinel_timeout
        self._sentinel_dir = tempfile.mkdtemp(prefix="pwnellij-zellij-")
        self._sizes: dict[str, list[int]] = {}
        self._main_id = self.backend.current_pane()
        self._main_tty = self.backend.main_tty()
        self.panes = [Split(self._main_id, None, "main", {})]
        atexit.register(self.close)

    # --- internals -------------------------------------------------------

    def _tty_of(self, split: Split | None) -> str | None:
        if split is not None and split.tty:
            return split.tty
        if split is None or split.id == self._main_id:
            return self._main_tty
        return None

    def _focus(self, pane_id: str) -> bool:
        """Cycle focus until ``pane_id`` is the focused pane.

        ``new-pane`` has no ``--pane-id`` flag and zellij has no direct
        focus-by-id action, so this walks ``focus-next-pane`` and checks
        ``list-clients`` after each step. Returns False (with a warning) if a
        full cycle never reaches the target.
        """
        focused = self.backend.focused_pane()
        if focused == pane_id:
            return True
        start = focused
        for _ in range(_FOCUS_MAX_CYCLES):
            self.backend.run("action", "focus-next-pane")
            focused = self.backend.focused_pane()
            if focused == pane_id:
                return True
            if focused == start:
                break
        warnings.warn(f"zellij: could not focus pane {pane_id}", stacklevel=4)
        return False

    def _rename(self, pane_id: str, title: str) -> None:
        self.backend.run("action", "rename-pane", "-p", pane_id, title)

    def _apply_size(
        self,
        pane_id: str,
        tty: str,
        direction: str,
        size: str,
        base: list[int] | None,
    ) -> None:
        """Nudge the new pane toward the requested size with ``resize`` steps.

        ``size`` follows tmux semantics: ``"30%"`` of the target pane's
        pre-split extent (``base``), or an absolute number of columns/rows.
        Each step is re-measured through the pane tty; the loop stops when the
        size is reached, stops moving (zellij min/max), or stops improving.
        """
        axis = 0 if direction in ("Left", "Right") else 1
        measured = self.backend.tty_size(tty)
        if measured is None:
            return
        cur = measured[axis]
        spec = str(size)
        try:
            if spec.endswith("%"):
                # The split halved the target, so 2*cur approximates its
                # pre-split extent when it could not be measured directly.
                total = base[axis] if base else cur * 2
                desired = round(total * float(spec[:-1]) / 100)
            else:
                desired = int(spec)
        except ValueError:
            warnings.warn(f"zellij: unparseable size {size!r}", stacklevel=4)
            return
        edge = _SHARED_EDGE[direction]
        for _ in range(_RESIZE_MAX_STEPS):
            if cur == desired:
                return
            op = "increase" if cur < desired else "decrease"
            self.backend.run("action", "resize", op, edge, "-p", pane_id)
            measured = self.backend.tty_size(tty)
            if measured is None:
                return
            new = measured[axis]
            if new == cur:  # zellij refused to move the border; give up
                return
            if abs(new - desired) >= abs(cur - desired):
                if abs(new - desired) > abs(cur - desired):
                    # overshot past the closest reachable step — undo it
                    undo = "decrease" if op == "increase" else "increase"
                    self.backend.run("action", "resize", undo, edge, "-p", pane_id)
                return
            cur = new

    def _do_split(
        self,
        direction: str,
        target: Split | None = None,
        display: str | None = None,
        cmd: str = "/bin/cat -",
        use_stdin: bool = False,
        size: str | None = None,
        **kwargs: Any,
    ) -> Split:
        # new-pane always splits the focused pane, so focus the target first.
        # No target means the main (gdb) pane, mirroring tmux's active pane.
        target_id = target.id if target is not None else self._main_id
        self._focus(target_id)
        base = self.backend.tty_size(self._tty_of(target))
        sentinel = os.path.join(self._sentinel_dir, uuid4().hex)
        user_cmd = f"(cat)|{cmd}" if use_stdin else cmd
        helper = _HELPER + user_cmd
        cli = ["action", "new-pane", "-d", _REAL_DIRECTION.get(direction, direction)]
        if display:
            cli += ["--name", display]
        cli += ["--", "bash", "-c", helper, "bash", sentinel]
        self.backend.run(*cli)
        try:
            tty, pane_id, measured = self.backend.read_sentinel(
                sentinel, timeout=self.sentinel_timeout
            )
        except TimeoutError as err:
            warnings.warn(f"zellij pane: {err}", stacklevel=2)
            tty, pane_id, measured = None, "", [80, 24]
        if pane_id and direction in _REAL_DIRECTION:
            self.backend.run("action", "move-pane", "-p", pane_id, direction.lower())
        if pane_id and tty and size is not None:
            self._apply_size(pane_id, tty, direction, size, base)
            refreshed = self.backend.tty_size(tty)
            if refreshed:
                measured = refreshed
        if pane_id:
            self._sizes[pane_id] = measured
        return Split(pane_id, tty, display, kwargs)

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
            self._rename(
                split.id,
                ", ".join(sp.display for sp in self.panes if sp.id == split.id),
            )
        return split

    def split(
        self,
        direction: str,
        *,
        target: str | Split | None = None,
        display: str | None = None,
        cmd: str | None = None,
        use_stdin: bool = False,
        **kwargs: Any,
    ) -> Split:
        if isinstance(target, str):
            target = self.get(target)
        split = self._do_split(
            direction,
            target=target,
            display=display,
            cmd=cmd or self.cmd,
            use_stdin=use_stdin,
            **kwargs,
        )
        self.panes.append(split)
        return split

    def left(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split:
        return self.split("Left", target=of, display=display, **kwargs)

    def right(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split:
        return self.split("Right", target=of, display=display, **kwargs)

    def above(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split:
        return self.split("Up", target=of, display=display, **kwargs)

    def below(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split:
        return self.split("Down", target=of, display=display, **kwargs)

    def splits(self) -> list[Split]:
        return self.panes

    def size(self, split: Split) -> list[int]:
        measured = self.backend.tty_size(self._tty_of(split))
        if measured:
            self._sizes[split.id] = measured
            return measured
        return self._sizes.get(split.id, [80, 24])

    def finish(self, **kwargs: Any) -> None:
        # Land the user back on the gdb pane once the layout is built.
        self._focus(self._main_id)

    def do(
        self,
        show_titles: bool | str | None = None,
        set_title: str | None = None,
        target: str | Split | None = None,
    ) -> None:
        """Zellij-side configuration.

        Parameters
        ----------
        show_titles : bool|str
            Ignored — zellij always shows pane names when set. Accepted for
            API compatibility with :class:`Tmux`.
        set_title : str
            Renames ``target`` (or the current split if None).
        target : str|Split
            Display name or Split to act on.
        """
        if set_title is not None:
            split = self.get(target) if target is not None else None
            pane_id = split.id if split is not None else self._main_id
            self._rename(pane_id, set_title)

    def close(self) -> None:
        # Best effort: a failure means the pane (or the whole session) is
        # already gone, and close() runs at exit where any output would just
        # land in the user's shell.
        for pane_id in {p.id for p in self.panes if p.id and p.id != self._main_id}:
            with contextlib.suppress(CalledProcessError, OSError):
                self.backend.run("action", "close-pane", "-p", pane_id)
        if os.path.exists(self._sentinel_dir):
            shutil.rmtree(self._sentinel_dir, ignore_errors=True)

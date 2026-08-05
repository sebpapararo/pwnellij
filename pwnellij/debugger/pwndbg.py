from typing import Any

from pwnellij.models import Debugger, Multiplexer


class Pwndbg(Debugger):
    def setup(
        self,
        multiplexer: Multiplexer,
        nobanner: bool | None = None,
        **_: Any,
    ) -> None:
        """Bind pwndbg context sections to each split's TTY.

        Imports pwndbg lazily so the module can be inspected outside GDB; a
        clear error is raised here if pwndbg is unavailable when setup runs.
        """
        try:
            import gdb
            import pwndbg
            from pwndbg.commands.context import clear_screen, contextoutput
        except ImportError as e:
            raise RuntimeError(
                "pwnellij Pwndbg debugger requires pwndbg; load this inside GDB "
                "with pwndbg available, or supply a different debugger"
            ) from e

        splits = list(multiplexer.splits())

        # Route the debugged program's own stdin/stdout to a dedicated pane when
        # one is marked with inferior=True; gdb otherwise runs the program on its
        # own terminal (the main pane). This takes effect on the next `run`,
        # which is exactly when build() runs — before the program is started.
        for split in splits:
            if split.settings.get("inferior") and split.tty:
                gdb.execute(f"set inferior-tty {split.tty}")
                break
        if nobanner:
            for split in splits:
                split.settings.setdefault("banner", "none")
        for split in splits:
            if split.display is not None and split.tty is not None:
                # `inferior` and `clearing` are consumed by pwnellij itself, and
                # contextoutput has no **kwargs to absorb them; clearing is its
                # third positional parameter, passed explicitly below.
                settings = {
                    k: v for k, v in split.settings.items() if k not in ("inferior", "clearing")
                }
                contextoutput(
                    split.display,
                    split.tty,
                    split.settings.get("clearing", True),
                    **settings,
                )

        panes = [s for s in splits if s.tty is not None]
        for tty in {p.tty for p in panes if p.settings.get("clearing", True)}:
            with open(tty, "w") as out:
                clear_screen(out)
        if not nobanner:
            for pane in (p for p in panes if p.display is not None):
                width, _ = multiplexer.size(pane)
                with open(pane.tty, "w") as out:
                    out.write(pwndbg.ui.banner(pane.display, target=out, width=width) + "\n")
                    out.flush()

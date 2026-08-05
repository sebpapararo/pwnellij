import sys
from collections.abc import Callable
from typing import Any

from .debugger.pwndbg import Pwndbg
from .models import Debugger, Multiplexer, Split
from .multiplexer.zellij import Zellij


class _FallbackMultiplexer(Multiplexer):
    """No-op backend used when the real multiplexer cannot be created.

    Every layout call is swallowed so a fluent ``Layout`` chain still runs to
    completion. ``Layout.build`` skips the debugger wiring in this mode, leaving
    the debugger (e.g. pwndbg) to render its context inline as it normally
    would when pwnellij is not in play.
    """

    def _split(self, display: str | None = None) -> Split:
        return Split("", None, display, {})

    def left(self, *args: Any, display: str | None = None, **kwargs: Any) -> Split:
        return self._split(display)

    def right(self, *args: Any, display: str | None = None, **kwargs: Any) -> Split:
        return self._split(display)

    def above(self, *args: Any, display: str | None = None, **kwargs: Any) -> Split:
        return self._split(display)

    def below(self, *args: Any, display: str | None = None, **kwargs: Any) -> Split:
        return self._split(display)

    def show(self, display: str, on: Any = None, **kwargs: Any) -> Split:
        return self._split(display)

    def get(self, display: str | Split) -> Split | None:
        return display if isinstance(display, Split) else None

    def splits(self) -> list[Split]:
        return []

    def size(self, split: Split) -> list[int]:
        return [0, 0]

    def finish(self, **kwargs: Any) -> None:
        pass

    def do(self, **kwargs: Any) -> None:
        pass

    def close(self) -> None:
        pass


class Layout:
    """Builder for a pwnellij layout.

    Splits always happen on the last created split unless an ``of`` is given or
    another split is selected. To split the starting point, use ``select(None)``
    or pass an ``of`` that has not yet been defined.

    Extra kwargs flow through to the multiplexer so backends can expose their own
    knobs. Anything the multiplexer does not consume is attached to the split's
    settings and forwarded to the debugger.
    """

    def __init__(
        self,
        multiplexer: Multiplexer | None = None,
        debugger: Debugger | None = None,
    ) -> None:
        self._fallback = False
        if multiplexer is not None:
            self.multiplexer = multiplexer
        else:
            try:
                self.multiplexer = Zellij()
            except Exception as err:  # e.g. not running inside zellij
                print(
                    f"pwnellij: failed to load the layout, falling back to inline "
                    f"debugger output ({err})",
                    file=sys.stderr,
                )
                self.multiplexer = _FallbackMultiplexer()
                self._fallback = True
        self.debugger = debugger if debugger is not None else Pwndbg()
        self.last = None

    def _wrap(
        self,
        method: Callable[..., Split],
        *args: Any,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> "Layout":
        if kwargs.get("inferior"):
            # The pane that receives the debugged program's I/O must idle
            # without reading stdin (so it does not steal the program's input)
            # and must not be cleared (so the program's output survives). Fill
            # in those defaults; the debugger reads `inferior` to point gdb's
            # inferior-tty at this pane.
            kwargs.setdefault("cmd", "tail -f /dev/null")
            kwargs.setdefault("clearing", False)
        self.last = method(*args, of=of or self.last, display=display, **kwargs)
        return self

    def left(self, *args: Any, **kwargs: Any) -> "Layout":
        return self._wrap(self.multiplexer.left, *args, **kwargs)

    def right(self, *args: Any, **kwargs: Any) -> "Layout":
        return self._wrap(self.multiplexer.right, *args, **kwargs)

    def above(self, *args: Any, **kwargs: Any) -> "Layout":
        return self._wrap(self.multiplexer.above, *args, **kwargs)

    def below(self, *args: Any, **kwargs: Any) -> "Layout":
        return self._wrap(self.multiplexer.below, *args, **kwargs)

    def show(
        self,
        display: str,
        on: str | Split | None = None,
        **kwargs: Any,
    ) -> "Layout":
        """Display ``display`` on an already-created split without creating a new one."""
        self.last = self.multiplexer.show(on=on or self.last, display=display, **kwargs)
        return self

    def select(self, display: str | Split | None) -> "Layout":
        """Selects ``display`` to continue from. Pass None for the main split."""
        self.last = None if display is None else self.multiplexer.get(display)
        return self

    def tell_multiplexer(
        self,
        target: str | Split | None = None,
        **kwargs: Any,
    ) -> "Layout":
        """Forwards configuration to the multiplexer. Honored keys are backend-specific."""
        if target is None:
            target = self.last
        self.multiplexer.do(target=target, **kwargs)
        return self

    def build(self, **kwargs: Any) -> None:
        """Builds the layout: finishes the multiplexer and lets the debugger bind output to splits.

        :param kwargs: forwarded to ``multiplexer.finish`` and ``debugger.setup``.
        """
        if self._fallback:
            return
        self.multiplexer.finish(**kwargs)
        self.debugger.setup(self.multiplexer, **kwargs)

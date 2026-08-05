from abc import ABC, abstractmethod
from typing import Any, NamedTuple


class Split(NamedTuple):
    """A pane capable of displaying information. Pure data; safe to copy.

    Sizing and other backend state live on the multiplexer, not here, so a Split
    can be passed around freely without dragging its backend with it.
    """

    id: str
    tty: str | None
    display: str | None
    settings: dict


class Multiplexer(ABC):
    """Layout backend contract. Implementations create panes and report their state."""

    @abstractmethod
    def left(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split: ...
    @abstractmethod
    def right(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split: ...
    @abstractmethod
    def above(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split: ...
    @abstractmethod
    def below(
        self,
        *args: str,
        of: str | Split | None = None,
        display: str | None = None,
        **kwargs: Any,
    ) -> Split: ...

    @abstractmethod
    def show(
        self,
        display: str,
        on: str | Split | None = None,
        **kwargs: Any,
    ) -> Split: ...

    @abstractmethod
    def get(self, display: str | Split) -> Split | None: ...

    @abstractmethod
    def splits(self) -> list[Split]: ...

    @abstractmethod
    def size(self, split: Split) -> list[int]:
        """Return [width, height] in cells for the given split."""

    @abstractmethod
    def finish(self, **kwargs: Any) -> None: ...

    @abstractmethod
    def do(self, **kwargs: Any) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class Debugger(ABC):
    """Content producer contract. Binds a debugger's outputs to a multiplexer's panes."""

    @abstractmethod
    def setup(self, multiplexer: Multiplexer, **kwargs: Any) -> None: ...

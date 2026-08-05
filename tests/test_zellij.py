import pytest

from pwnellij.models import Multiplexer, Split
from pwnellij.multiplexer.zellij import Zellij, ZellijBackend


class FakeZellijBackend:
    """In-memory stand-in for ZellijBackend. Records every run() call.

    Simulates just enough zellij behavior for the emulation layers:
    ``focused`` tracks the focused pane (new-pane focuses the new pane,
    focus-next-pane walks ``ring``), and ``tty_sizes`` maps tty paths to
    ``[cols, rows]`` that resize actions mutate by ``resize_step``.
    """

    def __init__(
        self,
        in_zellij: bool = True,
        current: str = "0",
        sentinel_tty: str = "/dev/pts/3",
        sentinel_size: list[int] | None = None,
        sentinel_raises: bool = False,
        ring: list[str] | None = None,
        main_tty: str | None = "/dev/pts/0",
        tty_sizes: dict[str, list[int]] | None = None,
        resize_step: int = 4,
    ):
        self._in_zellij = in_zellij
        self._current = current
        self.sentinel_tty = sentinel_tty
        self.sentinel_size = sentinel_size or [100, 30]
        self.sentinel_raises = sentinel_raises
        self.calls: list[tuple[str, ...]] = []
        self.pane_counter = 0
        self.focused = current
        self.ring = ring or []
        self._main_tty = main_tty
        self.tty_sizes = {k: list(v) for k, v in (tty_sizes or {}).items()}
        self.resize_step = resize_step

    def in_zellij(self) -> bool:
        return self._in_zellij

    def current_pane(self) -> str:
        return self._current

    def focused_pane(self) -> str | None:
        return self.focused

    def main_tty(self) -> str | None:
        return self._main_tty

    def tty_size(self, tty):
        if tty in self.tty_sizes:
            return list(self.tty_sizes[tty])
        return None

    def run(self, *args: str) -> bytes:
        self.calls.append(args)
        if args[:2] == ("action", "new-pane"):
            self.focused = str(self.pane_counter + 1)
        elif args[:2] == ("action", "focus-next-pane") and self.ring:
            try:
                i = self.ring.index(self.focused)
            except ValueError:
                i = -1
            self.focused = self.ring[(i + 1) % len(self.ring)]
        elif args[:2] == ("action", "resize") and self.sentinel_tty in self.tty_sizes:
            op, edge = args[2], args[3]
            axis = 0 if edge in ("left", "right") else 1
            delta = self.resize_step if op == "increase" else -self.resize_step
            self.tty_sizes[self.sentinel_tty][axis] += delta
        return b""

    def read_sentinel(self, path, timeout=2.0, interval=0.05):
        if self.sentinel_raises:
            raise TimeoutError(f"sentinel never populated: {path}")
        self.pane_counter += 1
        return self.sentinel_tty, str(self.pane_counter), list(self.sentinel_size)


# ----- Zellij construction -------------------------------------------------


def test_zellij_raises_outside_zellij():
    with pytest.raises(RuntimeError, match="ZELLIJ"):
        Zellij(backend=FakeZellijBackend(in_zellij=False))


def test_zellij_seeds_main_pane_from_current():
    z = Zellij(backend=FakeZellijBackend(current="7"))
    assert len(z.panes) == 1
    assert z.panes[0].id == "7"
    assert z.panes[0].display == "main"
    assert z.panes[0].tty is None


def test_zellij_is_a_multiplexer():
    assert isinstance(Zellij(backend=FakeZellijBackend()), Multiplexer)


def test_real_backend_in_zellij_reads_env(monkeypatch):
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_PANE_ID", "terminal_42")
    assert ZellijBackend().in_zellij() is True
    # "terminal_" prefix is stripped — zellij --pane-id wants the bare integer.
    assert ZellijBackend().current_pane() == "42"


def test_real_backend_outside_zellij(monkeypatch):
    monkeypatch.delenv("ZELLIJ", raising=False)
    assert ZellijBackend().in_zellij() is False


# ----- Directional splits ---------------------------------------------------
# new-pane only implements Right and Down; Left/Up splits go Right/Down and
# are then swapped into place with `move-pane --pane-id`.


@pytest.mark.parametrize(
    ("method", "expected_direction"),
    [("left", "Right"), ("right", "Right"), ("above", "Down"), ("below", "Down")],
)
def test_directional_splits_use_supported_direction(method, expected_direction):
    backend = FakeZellijBackend()
    z = Zellij(backend=backend)
    getattr(z, method)(display="regs")
    new_pane_call = next(c for c in backend.calls if c[:2] == ("action", "new-pane"))
    idx = new_pane_call.index("-d")
    assert new_pane_call[idx + 1] == expected_direction


@pytest.mark.parametrize(
    ("method", "move_direction"),
    [("left", "left"), ("above", "up")],
)
def test_left_and_above_swap_with_move_pane(method, move_direction):
    backend = FakeZellijBackend()
    z = Zellij(backend=backend)
    getattr(z, method)(display="regs")
    assert ("action", "move-pane", "-p", "1", move_direction) in backend.calls


@pytest.mark.parametrize("method", ["right", "below"])
def test_right_and_below_do_not_move_pane(method):
    backend = FakeZellijBackend()
    z = Zellij(backend=backend)
    getattr(z, method)(display="regs")
    assert not any(c[:2] == ("action", "move-pane") for c in backend.calls)


def test_split_records_pane_id_and_tty_from_sentinel():
    backend = FakeZellijBackend(sentinel_tty="/dev/pts/9", sentinel_size=[120, 30])
    z = Zellij(backend=backend)
    split = z.right(display="regs")
    assert split.id == "1"
    assert split.tty == "/dev/pts/9"


def test_split_omits_name_flag_when_display_none():
    backend = FakeZellijBackend()
    Zellij(backend=backend).right()
    new_pane = next(c for c in backend.calls if c[:2] == ("action", "new-pane"))
    assert "--name" not in new_pane


def test_use_stdin_wraps_with_cat_pipe():
    backend = FakeZellijBackend()
    Zellij(backend=backend).right(display="regs", cmd="grep foo", use_stdin=True)
    new_pane = next(c for c in backend.calls if c[:2] == ("action", "new-pane"))
    helper = new_pane[new_pane.index("-c") + 1]
    assert "(cat)|grep foo" in helper


# ----- Targeting (of=) via focus cycling ------------------------------------


def test_split_cycles_focus_to_target():
    backend = FakeZellijBackend(ring=["0", "1", "2"])
    z = Zellij(backend=backend)
    regs = z.right(display="regs")  # focused: 1
    z.right(display="stack")  # focused: 2
    backend.calls.clear()
    z.right(of=regs, display="extra")
    cycles = [c for c in backend.calls if c[:2] == ("action", "focus-next-pane")]
    # 2 -> 0 -> 1(regs): two steps, then the split happens off regs
    assert len(cycles) == 2
    assert backend.calls.index(cycles[-1]) < backend.calls.index(
        next(c for c in backend.calls if c[:2] == ("action", "new-pane"))
    )


def test_split_targets_main_when_of_is_none():
    backend = FakeZellijBackend(ring=["0", "1"])
    z = Zellij(backend=backend)
    z.right(display="regs")  # focused: 1
    backend.calls.clear()
    z.right(display="stack")  # of=None -> main ("0")
    cycles = [c for c in backend.calls if c[:2] == ("action", "focus-next-pane")]
    assert len(cycles) == 1
    assert backend.focused == "2"  # new-pane moved focus to the new pane


def test_split_skips_cycling_when_target_already_focused():
    backend = FakeZellijBackend(ring=["0", "1"])
    z = Zellij(backend=backend)
    backend.calls.clear()
    z.right(display="regs")  # of=None -> main, already focused
    assert not any(c[:2] == ("action", "focus-next-pane") for c in backend.calls)


def test_split_warns_when_target_unreachable():
    backend = FakeZellijBackend(ring=["0", "1"])
    z = Zellij(backend=backend)
    ghost = Split("9", "/dev/pts/9", "ghost", {})
    with pytest.warns(UserWarning, match="could not focus"):
        z.right(of=ghost, display="regs")
    # best effort: the split still happens (off whatever ended up focused)
    assert any(c[:2] == ("action", "new-pane") for c in backend.calls)


# ----- size= emulation via resize steps -------------------------------------


def test_split_size_percentage_resizes_toward_target():
    # main is 80 cols; 30% -> 24. New pane starts at 40, step is 4 -> 4 steps.
    backend = FakeZellijBackend(
        tty_sizes={"/dev/pts/0": [80, 24], "/dev/pts/3": [40, 24]},
    )
    z = Zellij(backend=backend)
    z.right(display="regs", size="30%")
    resizes = [c for c in backend.calls if c[:2] == ("action", "resize")]
    assert resizes == [("action", "resize", "decrease", "left", "-p", "1")] * 4
    assert backend.tty_sizes["/dev/pts/3"][0] == 24


def test_split_size_absolute_value():
    backend = FakeZellijBackend(
        tty_sizes={"/dev/pts/0": [80, 24], "/dev/pts/3": [40, 24]},
    )
    z = Zellij(backend=backend)
    z.right(display="regs", size="44")
    resizes = [c for c in backend.calls if c[:2] == ("action", "resize")]
    assert resizes == [("action", "resize", "increase", "left", "-p", "1")]
    assert backend.tty_sizes["/dev/pts/3"][0] == 44


def test_split_size_vertical_uses_rows_and_shared_edge():
    # below: the shared border is the new pane's top edge.
    backend = FakeZellijBackend(
        sentinel_size=[80, 12],
        tty_sizes={"/dev/pts/0": [80, 24], "/dev/pts/3": [80, 12]},
    )
    z = Zellij(backend=backend)
    z.below(display="stack", size="75%")  # 75% of 24 rows -> 18; 12 -> +4 -> 16
    resizes = [c for c in backend.calls if c[:2] == ("action", "resize")]
    assert all(c[2:4] == ("increase", "up") for c in resizes)
    assert backend.tty_sizes["/dev/pts/3"][1] in (16, 20)  # nearest reachable steps


def test_split_size_settles_on_nearest_step():
    # desired 30 from 40 with step 4: 40->36->32->28, |28-30| == |32-30| -> stop.
    backend = FakeZellijBackend(
        tty_sizes={"/dev/pts/0": [80, 24], "/dev/pts/3": [40, 24]},
    )
    z = Zellij(backend=backend)
    z.right(display="regs", size="37.5%")
    assert backend.tty_sizes["/dev/pts/3"][0] in (28, 32)
    resizes = [c for c in backend.calls if c[:2] == ("action", "resize")]
    assert len(resizes) <= 4  # no oscillation


def test_split_size_stops_when_resize_has_no_effect():
    backend = FakeZellijBackend(
        tty_sizes={"/dev/pts/0": [80, 24], "/dev/pts/3": [40, 24]},
        resize_step=0,  # zellij refuses to move the border (min/max size)
    )
    z = Zellij(backend=backend)
    z.right(display="regs", size="30%")
    resizes = [c for c in backend.calls if c[:2] == ("action", "resize")]
    assert len(resizes) == 1


def test_split_size_skipped_when_tty_unmeasurable():
    backend = FakeZellijBackend()  # no tty_sizes entries at all
    z = Zellij(backend=backend)
    z.right(display="regs", size="30%")
    assert not any(c[:2] == ("action", "resize") for c in backend.calls)


def test_split_consumes_size_kwarg():
    # size must not leak into Split.settings and reach pwndbg as a kwarg.
    backend = FakeZellijBackend()
    split = Zellij(backend=backend).right(display="regs", size="35%")
    assert "size" not in split.settings


# ----- show / size ----------------------------------------------------------


def test_show_clones_underlying_pane():
    z = Zellij(backend=FakeZellijBackend())
    orig = z.right(display="regs")
    cloned = z.show("legend", on="regs")
    assert cloned.id == orig.id
    assert cloned.tty == orig.tty
    assert cloned.display == "legend"


def test_show_renames_targeted_pane():
    backend = FakeZellijBackend()
    z = Zellij(backend=backend)
    z.right(display="regs")
    backend.calls.clear()
    z.show("legend", on="regs")
    assert ("action", "rename-pane", "-p", "1", "regs, legend") in backend.calls


def test_size_measures_live_through_tty():
    backend = FakeZellijBackend(
        sentinel_size=[120, 30],
        tty_sizes={"/dev/pts/3": [55, 20]},
    )
    z = Zellij(backend=backend)
    split = z.right(display="regs")
    assert z.size(split) == [55, 20]


def test_size_falls_back_to_sentinel_value():
    backend = FakeZellijBackend(sentinel_size=[120, 30])  # tty not measurable
    z = Zellij(backend=backend)
    split = z.right(display="regs")
    assert z.size(split) == [120, 30]


def test_size_returns_default_for_unknown_split():
    z = Zellij(backend=FakeZellijBackend(main_tty=None))
    assert z.size(z.panes[0]) == [80, 24]


# ----- do / finish / close ---------------------------------------------------


def test_do_set_title_renames_by_pane_id():
    backend = FakeZellijBackend()
    z = Zellij(backend=backend)
    z.right(display="regs")
    backend.calls.clear()
    z.do(set_title="Hello", target="regs")
    assert ("action", "rename-pane", "-p", "1", "Hello") in backend.calls


def test_do_set_title_defaults_to_main_pane():
    backend = FakeZellijBackend(current="7")
    z = Zellij(backend=backend)
    backend.calls.clear()
    z.do(set_title="Main")
    assert ("action", "rename-pane", "-p", "7", "Main") in backend.calls


def test_finish_refocuses_main_pane():
    backend = FakeZellijBackend(ring=["0", "1"])
    z = Zellij(backend=backend)
    z.right(display="regs")  # focused: 1
    backend.calls.clear()
    z.finish()
    assert ("action", "focus-next-pane") in backend.calls
    assert backend.focused == "0"


def test_close_closes_created_panes_but_not_main():
    backend = FakeZellijBackend(current="0", ring=["0", "1", "2"])
    z = Zellij(backend=backend)
    z.right(display="a")
    z.right(display="b")
    z.show("legend", on="a")  # clone of pane 1: must not close it twice
    backend.calls.clear()
    z.close()
    closes = sorted(c for c in backend.calls if c[:2] == ("action", "close-pane"))
    assert closes == [
        ("action", "close-pane", "-p", "1"),
        ("action", "close-pane", "-p", "2"),
    ]


def test_sentinel_timeout_yields_none_tty():
    backend = FakeZellijBackend(sentinel_raises=True)
    z = Zellij(backend=backend)
    with pytest.warns(UserWarning, match="sentinel"):
        split = z.right(display="regs")
    assert split.tty is None
    assert z.size(split) == [80, 24]
    # no pane id -> nothing to move, resize, or close
    assert not any(c[:2] == ("action", "move-pane") for c in backend.calls)
    z.close()
    assert not any(c[:2] == ("action", "close-pane") for c in backend.calls)

import pytest

from pwnellij.models import Multiplexer
from pwnellij.multiplexer.tmux import Tmux, TmuxBackend, _decode


class FakeBackend:
    """In-memory stand-in for TmuxBackend. Records every run() call."""

    def __init__(self, in_tmux=True, current="%0", options=b"", mouse=b"off\n"):
        self._in_tmux = in_tmux
        self._current = current
        self.calls: list[tuple[str, ...]] = []
        self.split_counter = 0
        self.options = options
        self.mouse = mouse
        self.size_response = b"80:24\n"

    def in_tmux(self) -> bool:
        return self._in_tmux

    def current_pane(self) -> str:
        return self._current

    def run(self, *args: str) -> bytes:
        self.calls.append(args)
        cmd = args[0] if args else ""
        if cmd == "split-window":
            self.split_counter += 1
            return f"%{self.split_counter}:/dev/pts/{self.split_counter}\n".encode()
        if cmd == "show-options":
            if "-v" in args:
                return self.mouse
            return self.options
        if cmd == "display":
            return self.size_response
        return b""


# ----- _decode -------------------------------------------------------------


def test_decode_handles_bytes():
    assert _decode(b"a:b:c") == ["a", "b", "c"]


def test_decode_handles_str():
    assert _decode("a:b:c") == ["a", "b", "c"]


def test_decode_strips_whitespace():
    assert _decode(b"  hello:world  \n") == ["hello", "world"]


def test_decode_respects_custom_delimiter():
    assert _decode(b"a\nb\nc", delimiter="\n") == ["a", "b", "c"]


# ----- Tmux construction ---------------------------------------------------


def test_tmux_raises_outside_tmux():
    with pytest.raises(RuntimeError, match="TMUX_PANE"):
        Tmux(backend=FakeBackend(in_tmux=False))


def test_tmux_seeds_main_pane_from_current():
    tmux = Tmux(backend=FakeBackend(current="%42"))
    assert len(tmux.panes) == 1
    assert tmux.panes[0].id == "%42"
    assert tmux.panes[0].display == "main"


def test_tmux_is_a_multiplexer():
    assert isinstance(Tmux(backend=FakeBackend()), Multiplexer)


def test_real_backend_in_tmux_reads_env(monkeypatch):
    monkeypatch.setenv("TMUX_PANE", "%99")
    assert TmuxBackend().in_tmux() is True
    assert TmuxBackend().current_pane() == "%99"


def test_real_backend_outside_tmux(monkeypatch):
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert TmuxBackend().in_tmux() is False


# ----- Directional splits emit the right tmux flag -------------------------


@pytest.mark.parametrize(
    ("method", "expected_flag"),
    [("left", "-hb"), ("right", "-h"), ("above", "-vb"), ("below", "-v")],
)
def test_directional_splits_use_expected_flag(method, expected_flag):
    backend = FakeBackend()
    tmux = Tmux(backend=backend)
    getattr(tmux, method)(display="regs")
    split_call = next(c for c in backend.calls if c[0] == "split-window")
    assert expected_flag in split_call
    # Make sure -h and -hb (and -v / -vb) don't both fire
    if expected_flag == "-h":
        assert "-hb" not in split_call
    if expected_flag == "-v":
        assert "-vb" not in split_call


def test_split_appends_size_flag():
    backend = FakeBackend()
    Tmux(backend=backend).right(display="regs", size="35%")
    call = next(c for c in backend.calls if c[0] == "split-window")
    idx = call.index("-l")
    assert call[idx + 1] == "35%"


def test_split_appends_target_flag_when_of_is_given():
    backend = FakeBackend()
    tmux = Tmux(backend=backend)
    main = tmux.panes[0]
    backend.calls.clear()
    tmux.right(of=main, display="regs")
    call = next(c for c in backend.calls if c[0] == "split-window")
    idx = call.index("-t")
    assert call[idx + 1] == main.id


def test_use_stdin_prepends_cat_pipe():
    backend = FakeBackend()
    Tmux(backend=backend).right(display="regs", cmd="grep foo", use_stdin=True)
    call = next(c for c in backend.calls if c[0] == "split-window")
    assert call[-1] == "(cat)|grep foo"


# ----- get / show / size ---------------------------------------------------


def test_get_returns_pane_by_display_name():
    backend = FakeBackend()
    tmux = Tmux(backend=backend)
    tmux.right(display="regs")
    assert tmux.get("regs").display == "regs"


def test_get_returns_none_when_missing():
    assert Tmux(backend=FakeBackend()).get("nope") is None


def test_get_passes_split_through_unchanged():
    tmux = Tmux(backend=FakeBackend())
    split = tmux.right(display="regs")
    assert tmux.get(split) is split


def test_show_clones_underlying_pane():
    tmux = Tmux(backend=FakeBackend())
    orig = tmux.right(display="regs")
    cloned = tmux.show("legend", on="regs")
    assert cloned.id == orig.id
    assert cloned.tty == orig.tty
    assert cloned.display == "legend"


def test_size_parses_width_and_height():
    backend = FakeBackend()
    backend.size_response = b"120:30\n"
    tmux = Tmux(backend=backend)
    split = tmux.right(display="regs")
    assert tmux.size(split) == [120, 30]


# ----- do / finish / close -------------------------------------------------


def test_do_set_title_invokes_select_pane():
    backend = FakeBackend()
    tmux = Tmux(backend=backend)
    tmux.right(display="regs")
    backend.calls.clear()
    tmux.do(set_title="Hello", target="regs")
    assert any(c[0] == "select-pane" and "-T" in c and "Hello" in c for c in backend.calls)


@pytest.mark.parametrize(
    ("show_titles", "expected"),
    [(True, "top"), ("bottom", "bottom"), (False, "off")],
)
def test_do_show_titles_sets_border_status(show_titles, expected):
    backend = FakeBackend()
    tmux = Tmux(backend=backend)
    backend.calls.clear()
    tmux.do(show_titles=show_titles)
    assert ("set", "pane-border-status", expected) in backend.calls


def test_finish_reselects_main_pane():
    backend = FakeBackend(current="%42")
    tmux = Tmux(backend=backend)
    tmux.right(display="regs")
    backend.calls.clear()
    tmux.finish()
    assert ("select-pane", "-t", "%42") in backend.calls


def test_close_kills_every_non_main_pane():
    backend = FakeBackend(current="%0")
    tmux = Tmux(backend=backend)
    tmux.right(display="a")
    tmux.right(display="b")
    backend.calls.clear()
    tmux.close()
    killed = {c[2] for c in backend.calls if c[0] == "kill-pane"}
    assert killed == {"%1", "%2"}


def test_close_restores_saved_window_options():
    backend = FakeBackend(options=b"opt1 val1\nopt2 val2")
    tmux = Tmux(backend=backend)
    backend.calls.clear()
    tmux.close()
    sets = [c for c in backend.calls if c[0] == "set"]
    assert ("set", "opt1", "val1") in sets
    assert ("set", "opt2", "val2") in sets
    # The synthesized "pane-border-status off" is also restored
    assert ("set", "pane-border-status", "off") in sets


def test_init_enables_mouse_by_default():
    backend = FakeBackend()
    Tmux(backend=backend)
    assert ("set", "mouse", "on") in backend.calls


def test_mouse_can_be_disabled():
    backend = FakeBackend()
    Tmux(backend=backend, mouse=False)
    assert not any(c[:2] == ("set", "mouse") for c in backend.calls)


def test_close_restores_prior_mouse_value():
    backend = FakeBackend(mouse=b"off\n")
    tmux = Tmux(backend=backend)
    backend.calls.clear()
    tmux.close()
    assert ("set", "mouse", "off") in backend.calls


def test_close_unsets_mouse_when_previously_unset():
    # An empty show-options value means mouse was never set on the session.
    # Restoring with `set mouse ""` would toggle it on; close() must unset
    # instead so the option falls back to the inherited value.
    backend = FakeBackend(mouse=b"")
    tmux = Tmux(backend=backend)
    backend.calls.clear()
    tmux.close()
    assert ("set", "-u", "mouse") in backend.calls
    assert ("set", "mouse", "") not in backend.calls


def test_close_leaves_mouse_alone_when_not_managed():
    backend = FakeBackend()
    tmux = Tmux(backend=backend, mouse=False)
    backend.calls.clear()
    tmux.close()
    assert not any(c[:2] == ("set", "mouse") for c in backend.calls)


def test_init_appends_pane_border_status_off_when_missing():
    backend = FakeBackend(options=b"opt1 val1")
    tmux = Tmux(backend=backend)
    assert "pane-border-status off" in tmux._saved_options


def test_init_preserves_existing_pane_border_status():
    backend = FakeBackend(options=b"pane-border-status top\nopt val")
    tmux = Tmux(backend=backend)
    # Should not append a second pane-border-status entry
    border_entries = [o for o in tmux._saved_options if o.startswith("pane-border-status")]
    assert border_entries == ["pane-border-status top"]

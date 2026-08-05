import pwnellij.layout as layout_module
from pwnellij.layout import Layout, _FallbackMultiplexer
from pwnellij.models import Debugger, Multiplexer, Split


class FakeMultiplexer(Multiplexer):
    """Records every Multiplexer call and synthesises plausible Split objects."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.panes_list: list[Split] = [Split("%0", None, "main", {})]
        self.finished_with: dict | None = None

    def _record(self, name, *args, **kwargs) -> Split:
        self.calls.append((name, args, kwargs))
        new_id = f"%{len(self.panes_list)}"
        new = Split(new_id, f"/dev/tty{len(self.panes_list)}", kwargs.get("display"), {})
        self.panes_list.append(new)
        return new

    def left(self, *args, of=None, display=None, **kwargs):
        return self._record("left", *args, of=of, display=display, **kwargs)

    def right(self, *args, of=None, display=None, **kwargs):
        return self._record("right", *args, of=of, display=display, **kwargs)

    def above(self, *args, of=None, display=None, **kwargs):
        return self._record("above", *args, of=of, display=display, **kwargs)

    def below(self, *args, of=None, display=None, **kwargs):
        return self._record("below", *args, of=of, display=display, **kwargs)

    def show(self, display, on=None, **kwargs):
        return self._record("show", on=on, display=display, **kwargs)

    def get(self, display):
        if isinstance(display, Split):
            return display
        return next((p for p in self.panes_list if p.display == display), None)

    def splits(self):
        return self.panes_list

    def size(self, split):
        return [80, 24]

    def finish(self, **kwargs):
        self.finished_with = kwargs

    def do(self, **kwargs):
        self.calls.append(("do", (), kwargs))

    def close(self):
        pass


class FakeDebugger(Debugger):
    def __init__(self):
        self.received_multiplexer: Multiplexer | None = None
        self.received_kwargs: dict | None = None

    def setup(self, multiplexer, **kwargs):
        self.received_multiplexer = multiplexer
        self.received_kwargs = kwargs


def _layout():
    fs, ft = FakeMultiplexer(), FakeDebugger()
    return Layout(multiplexer=fs, debugger=ft), fs, ft


# ----- construction --------------------------------------------------------


def test_layout_preserves_provided_instances():
    fs, ft = FakeMultiplexer(), FakeDebugger()
    m = Layout(multiplexer=fs, debugger=ft)
    assert m.multiplexer is fs
    assert m.debugger is ft
    assert m.last is None


# ----- chaining and last-tracking ------------------------------------------


def test_layout_left_forwards_display():
    m, fs, _ = _layout()
    m.left(display="regs")
    assert fs.calls[0][0] == "left"
    assert fs.calls[0][2]["display"] == "regs"


def test_layout_chain_passes_last_as_of():
    m, fs, _ = _layout()
    m.left(display="a").right(display="b")
    first_split = fs.panes_list[1]
    assert fs.calls[1][2]["of"] is first_split


def test_layout_explicit_of_overrides_last():
    m, fs, _ = _layout()
    m.left(display="a")
    m.right(of="main", display="b")
    assert fs.calls[1][2]["of"] == "main"


def test_layout_all_directional_methods_return_self_for_chaining():
    m, _, _ = _layout()
    assert m.left() is m
    assert m.right() is m
    assert m.above() is m
    assert m.below() is m
    assert m.select(None) is m
    assert m.tell_multiplexer() is m


def test_layout_show_returns_self_and_updates_last():
    m, _fs, _ = _layout()
    m.left(display="a")
    last_before = m.last
    result = m.show("legend")
    assert result is m
    assert m.last is not last_before  # new clone returned


# ----- inferior I/O pane ---------------------------------------------------


def test_layout_inferior_fills_idle_cmd_and_disables_clearing():
    m, fs, _ = _layout()
    m.above(inferior=True)
    name, _args, kwargs = fs.calls[0]
    assert name == "above"
    assert kwargs["inferior"] is True
    assert kwargs["cmd"] == "tail -f /dev/null"
    assert kwargs["clearing"] is False


def test_layout_inferior_respects_explicit_cmd_and_clearing():
    m, fs, _ = _layout()
    m.below(inferior=True, cmd="cat", clearing=True)
    _name, _args, kwargs = fs.calls[0]
    assert kwargs["cmd"] == "cat"
    assert kwargs["clearing"] is True


def test_layout_non_inferior_split_gets_no_idle_cmd():
    m, fs, _ = _layout()
    m.above(display="disasm")
    _name, _args, kwargs = fs.calls[0]
    assert "cmd" not in kwargs
    assert "clearing" not in kwargs


# ----- select --------------------------------------------------------------


def test_select_none_resets_last():
    m, _, _ = _layout()
    m.left(display="a")
    assert m.last is not None
    m.select(None)
    assert m.last is None


def test_select_by_name_uses_multiplexer_get():
    m, fs, _ = _layout()
    m.left(display="a")
    m.select("main")
    assert m.last is fs.panes_list[0]


# ----- tell_multiplexer ----------------------------------------------------


def test_tell_multiplexer_forwards_kwargs_to_do():
    m, fs, _ = _layout()
    m.tell_multiplexer(set_title="Hi")
    do_calls = [c for c in fs.calls if c[0] == "do"]
    assert do_calls
    assert do_calls[0][2]["set_title"] == "Hi"


def test_tell_multiplexer_target_defaults_to_last():
    m, fs, _ = _layout()
    m.left(display="a")
    last = m.last
    m.tell_multiplexer(set_title="X")
    do_calls = [c for c in fs.calls if c[0] == "do"]
    assert do_calls[-1][2]["target"] is last


# ----- build ---------------------------------------------------------------


def test_build_finishes_multiplexer_then_sets_up_debugger():
    m, fs, ft = _layout()
    m.build(nobanner=True)
    assert fs.finished_with == {"nobanner": True}
    assert ft.received_multiplexer is fs
    assert ft.received_kwargs == {"nobanner": True}


# ----- graceful fallback ---------------------------------------------------


def _boom(*_args, **_kwargs):
    raise RuntimeError("ZELLIJ is not set in the environment")


def test_layout_falls_back_when_default_multiplexer_fails(monkeypatch, capsys):
    monkeypatch.setattr(layout_module, "Zellij", _boom)
    m = Layout()
    assert m._fallback is True
    assert isinstance(m.multiplexer, _FallbackMultiplexer)
    err = capsys.readouterr().err
    assert "pwnellij: failed to load" in err
    assert "ZELLIJ" in err


def test_fallback_chain_runs_without_error(monkeypatch):
    monkeypatch.setattr(layout_module, "Zellij", _boom)
    # A full fluent chain must complete even though nothing is laid out.
    m = (
        Layout()
        .above(display="disasm", size="75%")
        .left(display="regs")
        .show("stack")
        .select(None)
        .tell_multiplexer(set_title="x")
    )
    assert isinstance(m, Layout)


def test_fallback_build_skips_debugger_setup(monkeypatch):
    monkeypatch.setattr(layout_module, "Zellij", _boom)
    ft = FakeDebugger()
    Layout(debugger=ft).above(display="disasm").build(nobanner=True)
    assert ft.received_multiplexer is None  # debugger never wired up


def test_explicit_multiplexer_is_never_overridden_by_fallback(monkeypatch):
    monkeypatch.setattr(layout_module, "Zellij", _boom)
    fs = FakeMultiplexer()
    m = Layout(multiplexer=fs)
    assert m._fallback is False
    assert m.multiplexer is fs

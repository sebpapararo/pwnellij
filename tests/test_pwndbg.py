import sys
import types

import pytest

from pwnellij.debugger.pwndbg import Pwndbg
from pwnellij.models import Split


class StubMultiplexer:
    def __init__(self, splits=None):
        self._splits = splits or [Split("%0", None, "main", {})]

    def splits(self):
        return self._splits

    def size(self, split):
        return [80, 24]


def _install_fake_pwndbg(monkeypatch):
    """Inject minimal fake gdb/pwndbg modules so setup() runs end-to-end.

    Returns ``(gdb, context)``; ``gdb.executed`` records every executed
    command string and ``context.contextoutput_calls`` every contextoutput
    call. The fake contextoutput mirrors the real pwndbg signature (no
    ``**kwargs``), so forwarding a pwnellij-internal settings key fails
    tests with a TypeError just as it would inside gdb.
    """
    gdb = types.ModuleType("gdb")
    gdb.executed = []
    gdb.execute = lambda cmd, *a, **k: gdb.executed.append(cmd)

    pwndbg = types.ModuleType("pwndbg")
    ui = types.ModuleType("pwndbg.ui")
    ui.banner = lambda *a, **k: "BANNER"
    pwndbg.ui = ui
    commands = types.ModuleType("pwndbg.commands")
    context = types.ModuleType("pwndbg.commands.context")

    def contextoutput(section, path, clearing, banner="both", width=None, height=None):
        context.contextoutput_calls.append((section, path, clearing, banner, width, height))

    context.contextoutput_calls = []
    context.contextoutput = contextoutput
    context.clear_screen = lambda *a, **k: None

    for name, mod in {
        "gdb": gdb,
        "pwndbg": pwndbg,
        "pwndbg.ui": ui,
        "pwndbg.commands": commands,
        "pwndbg.commands.context": context,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return gdb, context


def test_setup_raises_runtimeerror_when_pwndbg_unavailable(monkeypatch):
    # Blocking these in sys.modules makes future `import pwndbg` raise ImportError.
    monkeypatch.setitem(sys.modules, "pwndbg", None)
    monkeypatch.setitem(sys.modules, "pwndbg.commands", None)
    monkeypatch.setitem(sys.modules, "pwndbg.commands.context", None)
    with pytest.raises(RuntimeError, match="pwndbg"):
        Pwndbg().setup(StubMultiplexer())


def test_setup_routes_inferior_io_to_marked_pane(monkeypatch, tmp_path):
    gdb, _ = _install_fake_pwndbg(monkeypatch)
    tty = str(tmp_path / "io")  # a writable stand-in for the pane's /dev/pts/N
    splits = [
        Split("%0", None, "main", {}),
        Split("%1", tty, None, {"inferior": True, "clearing": False}),
    ]
    Pwndbg().setup(StubMultiplexer(splits), nobanner=True)
    assert f"set inferior-tty {tty}" in gdb.executed


def test_setup_without_inferior_pane_sets_no_tty(monkeypatch, tmp_path):
    gdb, _ = _install_fake_pwndbg(monkeypatch)
    tty = str(tmp_path / "regs")
    splits = [
        Split("%0", None, "main", {}),
        Split("%1", tty, "regs", {}),
    ]
    Pwndbg().setup(StubMultiplexer(splits), nobanner=True)
    assert not any("inferior-tty" in cmd for cmd in gdb.executed)


def test_setup_strips_consumed_keys_before_contextoutput(monkeypatch, tmp_path):
    # `inferior` and `clearing` are pwnellij's own settings; contextoutput has
    # no **kwargs, so forwarding them raised a TypeError. clearing must instead
    # land as the third positional parameter.
    _, context = _install_fake_pwndbg(monkeypatch)
    tty = str(tmp_path / "io")
    splits = [
        Split("%0", None, "main", {}),
        Split("%1", tty, "io", {"inferior": True, "clearing": False, "banner": "none"}),
    ]
    Pwndbg().setup(StubMultiplexer(splits), nobanner=True)
    assert context.contextoutput_calls == [("io", tty, False, "none", None, None)]


def test_setup_defaults_clearing_to_true(monkeypatch, tmp_path):
    _, context = _install_fake_pwndbg(monkeypatch)
    tty = str(tmp_path / "regs")
    splits = [
        Split("%0", None, "main", {}),
        Split("%1", tty, "regs", {}),
    ]
    Pwndbg().setup(StubMultiplexer(splits), nobanner=True)
    assert context.contextoutput_calls == [("regs", tty, True, "none", None, None)]

import pytest

from pwnellij.models import Debugger, Multiplexer, Split


def test_split_is_pure_data():
    s = Split("a", "/dev/pts/1", "regs", {})
    assert (s.id, s.tty, s.display, s.settings) == ("a", "/dev/pts/1", "regs", {})


def test_split_replace_preserves_other_fields():
    s = Split("a", "/dev/pts/1", "regs", {"banner": False})
    s2 = s._replace(display="stack")
    assert s2.display == "stack"
    assert s2.id == "a"
    assert s2.tty == "/dev/pts/1"
    assert s2.settings == {"banner": False}


def test_multiplexer_cannot_be_instantiated_without_methods():
    class Incomplete(Multiplexer):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()


def test_debugger_cannot_be_instantiated_without_methods():
    class Incomplete(Debugger):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()


def test_multiplexer_concrete_subclass_instantiates():
    class Concrete(Multiplexer):
        def left(self, *a, of=None, display=None, **k):
            return Split("x", None, display, {})

        def right(self, *a, of=None, display=None, **k):
            return Split("x", None, display, {})

        def above(self, *a, of=None, display=None, **k):
            return Split("x", None, display, {})

        def below(self, *a, of=None, display=None, **k):
            return Split("x", None, display, {})

        def show(self, display, on=None, **k):
            return Split("x", None, display, {})

        def get(self, display):
            return None

        def splits(self):
            return []

        def size(self, split):
            return [80, 24]

        def finish(self, **k):
            pass

        def do(self, **k):
            pass

        def close(self):
            pass

    Concrete()

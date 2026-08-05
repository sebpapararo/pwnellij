"""Tests for the bin/pwnellij launcher's argument scan.

The wrapper never launches here: PWNELLIJ_DRY_RUN makes it print the multiplexer,
title and pwndbg command it resolved and exit, so the parsing is exercised
without a real tmux or zellij session.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parent.parent / "bin" / "pwnellij"


def invoke(*args, **env):
    """Run the wrapper in dry-run mode and return the completed process."""
    return subprocess.run(
        [str(WRAPPER), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "PWNELLIJ_DRY_RUN": "1", "PWNELLIJ_MULTIPLEXER": "zellij", **env},
    )


def run(*args, **env):
    """Run the wrapper in dry-run mode and return (returncode, parsed fields, stderr)."""
    proc = invoke(*args, **env)
    fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    return proc.returncode, fields, proc.stderr


def test_binary_names_the_window():
    _, out, _ = run("./some/path/prog")
    assert out["title"] == "pwnellij: prog"


def test_no_arguments_leaves_a_plain_title():
    _, out, _ = run()
    assert out["title"] == "pwnellij"
    assert out["cmd"] == "pwndbg"


@pytest.mark.parametrize("flag", ["--pid", "-pid", "-p"])
def test_pid_as_a_separate_argument(flag):
    code, out, _ = run(flag, str(os.getpid()))
    assert code == 0
    assert f"({os.getpid()})" in out["title"]
    assert out["cmd"] == f"pwndbg {flag} {os.getpid()}"


@pytest.mark.parametrize("flag", ["--pid=", "-pid=", "-p"])
def test_pid_joined_to_the_flag(flag):
    code, out, _ = run(f"{flag}{os.getpid()}")
    assert code == 0
    assert f"({os.getpid()})" in out["title"]


def test_pid_title_carries_the_process_name():
    # The test runner stands in for the process being attached to.
    _, out, _ = run("--pid", str(os.getpid()))
    assert re.fullmatch(rf"pwnellij: \S.* \({os.getpid()}\)", out["title"])


def test_unknown_pid_fails_before_opening_a_window():
    code, out, err = run("--pid", "999999999")
    assert code == 1
    assert "no such process" in err
    assert out == {}


@pytest.mark.parametrize("value", ["notapid", ""])
def test_a_pid_that_is_not_a_number_is_rejected(value):
    # The empty case is what `--pid "$(pgrep something-that-is-not-running)"` gives.
    code, _, err = run("--pid", value)
    assert code == 1
    assert "wants a process id" in err


def test_option_values_are_not_mistaken_for_the_binary():
    _, out, _ = run("-ex", "break main", "-x", "/tmp/setup.gdb", "./prog")
    assert out["title"] == "pwnellij: prog"


@pytest.mark.parametrize("flag", ["--args", "-args"])
def test_args_takes_the_program_that_follows_it(flag):
    _, out, _ = run(flag, "./prog", "-v", "input.txt")
    assert out["title"] == "pwnellij: prog"


def test_inferior_arguments_are_not_read_as_gdb_options():
    # Everything after --args belongs to the debuggee. Scanning on would read
    # the program's own --pid as gdb's attach flag, and the unknown-pid check
    # would then refuse to launch a perfectly valid session.
    code, out, err = run("--args", "./prog", "--pid", "999999999")
    assert code == 0
    assert out["title"] == "pwnellij: prog"
    assert out["cmd"] == "pwndbg --args ./prog --pid 999999999"
    assert err == ""


def test_a_pid_before_args_is_still_recognized():
    _, out, _ = run("--pid", str(os.getpid()), "--args", "./prog")
    assert f"({os.getpid()})" in out["title"]


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_prints_usage_instead_of_launching(flag):
    proc = invoke(flag)
    assert proc.returncode == 0
    assert proc.stdout.startswith("pwnellij")
    assert "Usage:" in proc.stdout
    # Nothing was resolved: the wrapper exited before deciding on a multiplexer.
    assert "title=" not in proc.stdout


def test_help_wins_over_a_bad_pid():
    # Asking for help should never fail, whatever else is on the command line.
    proc = invoke("--pid", "999999999", "--help")
    assert proc.returncode == 0
    assert "Usage:" in proc.stdout
    assert proc.stderr == ""


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_after_args_belongs_to_the_debuggee(flag):
    code, out, _ = run("--args", "./prog", flag)
    assert code == 0
    assert out["title"] == "pwnellij: prog"
    assert out["cmd"] == f"pwndbg --args ./prog {flag}"


def test_help_as_a_gdb_command_value_is_not_intercepted():
    code, out, _ = run("-ex", "--help", "./prog")
    assert code == 0
    assert out["title"] == "pwnellij: prog"


def test_arguments_are_passed_through_quoted():
    _, out, _ = run("--args", "./my prog", "a b")
    assert out["cmd"] == "pwndbg --args './my prog' 'a b'"


def test_ordinary_arguments_are_left_unquoted():
    # Only words that need it get quotes, so the usual command stays readable.
    _, out, _ = run("-ex", "break main", "./prog")
    assert out["cmd"] == "pwndbg -ex 'break main' ./prog"


# tmux runs the command through the user's default-shell, which is not
# necessarily the shell that built it, so the quoting has to be shell-agnostic.
SHELLS = [s for s in (shutil.which("sh"), shutil.which("bash"), shutil.which("zsh")) if s]


@pytest.mark.parametrize("shell", SHELLS)
def test_quoting_round_trips_through_a_shell(shell):
    nasty = [
        "a b",
        "it's",
        'say "hi"',
        "$(id)",
        "back`tick`",
        "semi;colon",
        "new\nline",
        "tab\there",
        "back\\slash",
        "hash#mark",
        "bang!",
        "*",
        "~root",
        "",
    ]
    proc = invoke("--args", "./prog", *nasty)
    # Parsed by hand rather than through run(): the arguments contain newlines,
    # so the cmd= field is not confined to one line.
    payload = proc.stdout.split("cmd=", 1)[1].rstrip("\n")
    assert payload.startswith("pwndbg ")

    dump = 'eval "set -- $PAYLOAD"; exec python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" "$@"'  # noqa: E501
    parsed = subprocess.run(
        [shell, "-c", dump],
        capture_output=True,
        text=True,
        env={**os.environ, "PAYLOAD": payload.removeprefix("pwndbg ")},
    )
    assert parsed.returncode == 0, parsed.stderr
    assert json.loads(parsed.stdout) == ["--args", "./prog", *nasty]

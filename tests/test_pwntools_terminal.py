"""Tests for the bin/pwntools-terminal launcher.

Every case runs the real script with fake `zellij`/`tmux`/`x-terminal-emulator`
binaries on PATH; each records the argv and environment it was handed to a file,
so the dispatch can be checked without a session of either kind.
"""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WRAPPER = REPO / "bin" / "pwntools-terminal"


def shims(bin_dir, record, names=("zellij", "tmux", "x-terminal-emulator")):
    """Stand-ins for the terminal binaries, recording how they were called."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = bin_dir / name
        path.write_text(
            "#!/bin/sh\n"
            f'{{ printf "argv %s\\n" "$@"\n'
            f'  printf "TMUX=%s ZELLIJ=%s\\n" "${{TMUX-unset}}" "${{ZELLIJ-unset}}"\n'
            f'}} > "{record}"\n'
        )
        path.chmod(0o755)


def run(tmp_path, *args, shim_names=("zellij", "tmux", "x-terminal-emulator"), **env):
    record = tmp_path / "record.txt"
    bin_dir = tmp_path / "bin"
    shims(bin_dir, record, shim_names)
    proc = subprocess.run(
        # /bin/sh by absolute path: PATH holds only the shims, so that the
        # wrapper's `command -v` lookups see exactly what each test set up.
        ["/bin/sh", str(WRAPPER), *args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={"PATH": str(bin_dir), "HOME": str(tmp_path), **env},
    )
    lines = record.read_text().splitlines() if record.exists() else []
    argv = [line[len("argv ") :] for line in lines if line.startswith("argv ")]
    seen_env = next((line for line in lines if line.startswith("TMUX=")), "")
    return proc, argv, seen_env


def script(tmp_path, name="launch-gdb"):
    """An executable stand-in for the temp script pwntools generates."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


def test_inside_zellij_a_named_tab_is_opened_in_the_exploits_cwd(tmp_path):
    exe = script(tmp_path)
    proc, argv, _ = run(tmp_path, exe, ZELLIJ="0")
    assert proc.returncode == 0, proc.stderr
    # --cwd is the point: pwnlib's which() does not absolutize, so gdb.debug's
    # './binary' only resolves if the tab starts where the exploit ran.
    assert argv == [
        "action",
        "new-tab",
        "--cwd",
        str(tmp_path),
        "--name",
        "pwntools-gdb",
        "--close-on-exit",
        "--",
        exe,
    ]


def test_inside_tmux_a_named_window_is_opened_in_the_exploits_cwd(tmp_path):
    exe = script(tmp_path)
    proc, argv, _ = run(tmp_path, exe, TMUX="/tmp/tmux-1000/default,123,0")
    assert proc.returncode == 0, proc.stderr
    assert argv == ["new-window", "-c", str(tmp_path), "-n", "pwntools-gdb", "--", exe]


def test_zellij_wins_when_both_are_set_and_the_tmux_env_is_stripped(tmp_path):
    exe = script(tmp_path)
    proc, argv, seen_env = run(tmp_path, exe, ZELLIJ="0", TMUX="/tmp/tmux-1000/default,123,0")
    assert proc.returncode == 0, proc.stderr
    assert argv[:2] == ["action", "new-tab"]
    # gdb starting in that tab must not see a stale TMUX and pick Tmux().
    assert seen_env == "TMUX=unset ZELLIJ=0"


def test_outside_a_multiplexer_it_falls_back_to_x_terminal_emulator(tmp_path):
    exe = script(tmp_path)
    proc, argv, _ = run(tmp_path, exe)
    assert proc.returncode == 0, proc.stderr
    assert argv == ["-e", exe]


def test_a_command_string_is_handed_to_a_shell(tmp_path):
    # run_in_new_terminal() also accepts a shell command string, which cannot be
    # exec'd as a program name.
    proc, argv, _ = run(tmp_path, "echo hello world", ZELLIJ="0")
    assert proc.returncode == 0, proc.stderr
    assert argv[-3:] == ["sh", "-c", "echo hello world"]


def test_an_executable_argument_is_not_wrapped_in_a_shell(tmp_path):
    exe = script(tmp_path)
    _proc, argv, _ = run(tmp_path, exe, ZELLIJ="0")
    assert "sh" not in argv


def test_no_multiplexer_and_no_terminal_is_a_clear_error(tmp_path):
    proc, argv, _ = run(tmp_path, script(tmp_path), shim_names=())
    assert proc.returncode == 1
    assert argv == []
    assert "no x-terminal-emulator" in proc.stderr
    assert "Remove this from your PATH" in proc.stderr


def test_no_arguments_is_rejected(tmp_path):
    proc, _argv, _ = run(tmp_path)
    assert proc.returncode == 2
    assert "nothing to run" in proc.stderr


def test_the_wrapper_is_executable_and_posix_sh():
    assert os.access(WRAPPER, os.X_OK), "bin/pwntools-terminal must be executable"
    assert WRAPPER.read_text().startswith("#!/bin/sh\n")

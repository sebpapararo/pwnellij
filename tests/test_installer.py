"""Tests for the scripts/install.sh installer.

Every case runs the installer from the checkout it lives in, with
PWNELLIJ_BIN_DIR and GDBINIT pointed inside tmp_path: nothing outside the
temporary directory is written, and no network access is needed because a local
checkout is used in place rather than cloned.
"""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "scripts" / "install.sh"


def install(tmp_path, **env):
    """Run the installer against this checkout, writing only inside tmp_path."""
    return subprocess.run(
        ["sh", str(INSTALLER)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PWNELLIJ_BIN_DIR": str(tmp_path / "bin"),
            "GDBINIT": str(tmp_path / "gdbinit"),
            "PWNELLIJ_MULTIPLEXER": "zellij",
            **env,
        },
    )


def test_a_local_checkout_is_used_in_place(tmp_path):
    proc = install(tmp_path, PWNELLIJ_NO_GDBINIT="1")
    assert proc.returncode == 0, proc.stderr
    assert f"Using existing checkout at {REPO}" in proc.stdout
    # resolve_dir used to be called as DIR=$(resolve_dir), so the LOCAL_CHECKOUT
    # it set was lost with the subshell and the installer went on to git-pull a
    # checkout it had just been told to use as is.
    assert "Cloning" not in proc.stdout
    assert "Updating existing checkout" not in proc.stdout
    assert (tmp_path / "bin" / "pwnellij").resolve() == REPO / "bin" / "pwnellij"


def test_the_gdbinit_block_is_added_then_updated_in_place(tmp_path):
    gdbinit = tmp_path / "gdbinit"
    gdbinit.write_text("set disassembly-flavor intel\n")

    first = install(tmp_path)
    assert first.returncode == 0, first.stderr
    assert "Added the pwnellij layout" in first.stdout

    second = install(tmp_path)
    assert second.returncode == 0, second.stderr
    assert "Updated the pwnellij layout" in second.stdout

    text = gdbinit.read_text()
    assert text.count("# >>> pwnellij >>>") == 1
    assert text.startswith("set disassembly-flavor intel\n")
    assert f"source {REPO}/gdbinit.py" in text
    # Re-runs used to stack a blank line above the block each time.
    assert "\n\n\n" not in text


def test_the_tmux_layout_names_tmux_explicitly(tmp_path):
    proc = install(tmp_path, PWNELLIJ_MULTIPLEXER="tmux")
    assert proc.returncode == 0, proc.stderr
    # bin/pwnellij greps for a Tmux( call to pick its backend, so the tmux
    # layout has to spell the constructor out rather than rely on the default.
    assert "pwnellij.Layout(multiplexer=pwnellij.Tmux())" in (tmp_path / "gdbinit").read_text()


def test_an_unknown_multiplexer_is_rejected(tmp_path):
    proc = install(tmp_path, PWNELLIJ_MULTIPLEXER="screen")
    assert proc.returncode == 1
    assert "must be 'tmux' or 'zellij'" in proc.stderr
    assert not (tmp_path / "gdbinit").exists()

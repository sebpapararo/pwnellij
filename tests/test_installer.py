"""Tests for the scripts/install.sh installer.

Every case runs the installer from the checkout it lives in, with
PWNELLIJ_BIN_DIR, GDBINIT and PWN_CONF pointed inside tmp_path: nothing outside
the temporary directory is written, and no network access is needed because a
local checkout is used in place rather than cloned.
"""

import configparser
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "scripts" / "install.sh"


def fake_bin(tmp_path, gdb_finds_pwndbg=True, pwndbg=False):
    """A PATH prefix holding a stub gdb, and optionally a pwndbg launcher.

    The installer probes the real gdb to decide whether pwntools needs pointing
    at a standalone pwndbg; stubbing it keeps that decision (and the test) off
    whatever the host happens to have installed.
    """
    d = tmp_path / "fakebin"
    d.mkdir(exist_ok=True)
    gdb = d / "gdb"
    gdb.write_text(
        "#!/bin/sh\necho PWNDBG_OK\n"
        if gdb_finds_pwndbg
        else "#!/bin/sh\necho 'Python Exception: ImportError' >&2\n"
    )
    gdb.chmod(0o755)
    if pwndbg:
        launcher = d / "pwndbg"
        launcher.write_text("#!/bin/sh\nexit 0\n")
        launcher.chmod(0o755)
    return d


def install(tmp_path, path_prefix=None, **env):
    """Run the installer against this checkout, writing only inside tmp_path."""
    prefix = path_prefix if path_prefix is not None else fake_bin(tmp_path)
    return subprocess.run(
        ["sh", str(INSTALLER)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{prefix}{os.pathsep}{os.environ['PATH']}",
            "PWNELLIJ_BIN_DIR": str(tmp_path / "bin"),
            "GDBINIT": str(tmp_path / "gdbinit"),
            "PWN_CONF": str(tmp_path / "pwn.conf"),
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


# ----- pwntools integration ------------------------------------------------


def test_the_pwntools_terminal_wrapper_is_linked(tmp_path):
    proc = install(tmp_path, PWNELLIJ_NO_GDBINIT="1")
    assert proc.returncode == 0, proc.stderr
    link = tmp_path / "bin" / "pwntools-terminal"
    assert link.resolve() == REPO / "bin" / "pwntools-terminal"
    assert os.access(link, os.X_OK)


def test_pwn_conf_points_pwntools_at_a_standalone_pwndbg(tmp_path):
    path_prefix = fake_bin(tmp_path, gdb_finds_pwndbg=False, pwndbg=True)
    proc = install(tmp_path, path_prefix=path_prefix, PWNELLIJ_NO_GDBINIT="1")
    assert proc.returncode == 0, proc.stderr

    conf = tmp_path / "pwn.conf"
    parsed = configparser.ConfigParser()
    parsed.read(conf)
    # Quoted: pwntools runs these values through safeeval, so a bare path is a
    # ValueError rather than a string.
    assert parsed["context"]["gdb_binary"] == f"'{path_prefix / 'pwndbg'}'"
    assert "Pointed pwntools at" in proc.stdout


def test_pwn_conf_is_untouched_when_gdb_already_has_pwndbg(tmp_path):
    proc = install(
        tmp_path,
        path_prefix=fake_bin(tmp_path, gdb_finds_pwndbg=True, pwndbg=True),
        PWNELLIJ_NO_GDBINIT="1",
    )
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "pwn.conf").exists()


def test_pwn_conf_gains_the_key_inside_an_existing_context_section(tmp_path):
    conf = tmp_path / "pwn.conf"
    conf.write_text("[log]\ninfo.color=blue\n\n[context]\ntimeout=60\n")
    proc = install(
        tmp_path,
        path_prefix=fake_bin(tmp_path, gdb_finds_pwndbg=False, pwndbg=True),
        PWNELLIJ_NO_GDBINIT="1",
    )
    assert proc.returncode == 0, proc.stderr

    text = conf.read_text()
    # A second [context] section would raise DuplicateSectionError in pwntools'
    # strict-mode configparser, breaking every `from pwn import *`.
    assert text.count("[context]") == 1
    parsed = configparser.ConfigParser()
    parsed.read(conf)  # strict by default: raises if the file was mangled
    assert parsed["context"]["timeout"] == "60"
    assert parsed["context"]["gdb_binary"].startswith("'")
    assert parsed["log"]["info.color"] == "blue"


def test_pwn_conf_keeps_a_gdb_binary_the_user_already_set(tmp_path):
    conf = tmp_path / "pwn.conf"
    conf.write_text("[context]\ngdb_binary='/opt/my/gdb'\n")
    proc = install(
        tmp_path,
        path_prefix=fake_bin(tmp_path, gdb_finds_pwndbg=False, pwndbg=True),
        PWNELLIJ_NO_GDBINIT="1",
    )
    assert proc.returncode == 0, proc.stderr
    assert conf.read_text() == "[context]\ngdb_binary='/opt/my/gdb'\n"
    assert "already sets gdb_binary" in proc.stdout


def test_no_pwntools_skips_both_the_link_and_the_config(tmp_path):
    proc = install(
        tmp_path,
        path_prefix=fake_bin(tmp_path, gdb_finds_pwndbg=False, pwndbg=True),
        PWNELLIJ_NO_GDBINIT="1",
        PWNELLIJ_NO_PWNTOOLS="1",
    )
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "bin" / "pwntools-terminal").exists()
    assert not (tmp_path / "pwn.conf").exists()
    assert (tmp_path / "bin" / "pwnellij").exists()  # the launcher still lands

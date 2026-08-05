#!/bin/sh
# pwnellij installer — clone pwnellij, put the `pwnellij` wrapper on your PATH,
# and wire a default layout into ~/.gdbinit, in one command:
#
#   curl -sSfL https://raw.githubusercontent.com/sebpapararo/pwnellij/main/scripts/install.sh | sh
#
# Re-running is safe: it updates the checkout (git pull) and rewrites the
# managed ~/.gdbinit block in place rather than duplicating it.
#
# Configure via environment variables (all optional):
#   PWNELLIJ_DIR          where to install the checkout
#                         (default: ${XDG_DATA_HOME:-~/.local/share}/pwnellij)
#   PWNELLIJ_BIN_DIR      where to symlink the wrapper (default: ~/.local/bin)
#   PWNELLIJ_MULTIPLEXER  zellij (default) or tmux — picks the layout written
#                         to ~/.gdbinit
#   PWNELLIJ_REPO         git URL to clone (default: the GitHub repo)
#   PWNELLIJ_REF          branch/tag to fetch (default: main)
#   PWNELLIJ_NO_GDBINIT   set to any value to install only, skipping ~/.gdbinit
#   PWNELLIJ_NO_PWNTOOLS  set to any value to skip the pwntools integration
#                         (the pwntools-terminal link and ~/.pwn.conf)
#   GDBINIT               path to the gdb init file (default: ~/.gdbinit)
#   PWN_CONF              path to pwntools' config file (default: ~/.pwn.conf)
set -eu

REPO="${PWNELLIJ_REPO:-https://github.com/sebpapararo/pwnellij}"
REF="${PWNELLIJ_REF:-main}"
BIN_DIR="${PWNELLIJ_BIN_DIR:-$HOME/.local/bin}"
MUX="${PWNELLIJ_MULTIPLEXER:-zellij}"
GDBINIT="${GDBINIT:-$HOME/.gdbinit}"
PWN_CONF="${PWN_CONF:-$HOME/.pwn.conf}"
MARK_START="# >>> pwnellij >>>"
MARK_END="# <<< pwnellij <<<"

if [ -t 1 ]; then
    BOLD=$(printf '\033[1m'); GREEN=$(printf '\033[32m')
    YELLOW=$(printf '\033[33m'); RED=$(printf '\033[31m'); RESET=$(printf '\033[0m')
else
    BOLD=; GREEN=; YELLOW=; RED=; RESET=
fi
say()  { printf '%s==>%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%swarning:%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
err()  { printf '%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Where to install. Honor PWNELLIJ_DIR; otherwise, if this script is being run
# from inside an existing checkout (scripts/install.sh), use that checkout as
# is; otherwise fall back to the XDG data dir and clone there.
#
# This assigns DIR and LOCAL_CHECKOUT instead of printing the directory: a
# `DIR=$(resolve_dir)` call would run the function in a subshell, and the
# LOCAL_CHECKOUT it sets there would be lost -- leaving the caller to git-pull a
# checkout it was told to use in place.
LOCAL_CHECKOUT=0
DIR=""
resolve_dir() {
    if [ -n "${PWNELLIJ_DIR:-}" ]; then
        DIR="$PWNELLIJ_DIR"
        return
    fi
    case "$0" in
        */*)
            sd=$(CDPATH='' cd -- "$(dirname -- "$0")" 2>/dev/null && pwd) || sd=''
            if [ -n "$sd" ] && [ -f "$sd/../gdbinit.py" ]; then
                LOCAL_CHECKOUT=1
                DIR=$(CDPATH='' cd -- "$sd/.." && pwd)
                return
            fi
            ;;
    esac
    DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pwnellij"
}

fetch_repo() {
    dir="$1"
    if [ "$LOCAL_CHECKOUT" = 1 ]; then
        say "Using existing checkout at $dir"
        return
    fi
    if [ -d "$dir/.git" ]; then
        if have git; then
            say "Updating existing checkout in $dir"
            git -C "$dir" pull --ff-only || warn "git pull failed; keeping the existing checkout"
        else
            warn "git not found; keeping the existing checkout in $dir unchanged"
        fi
        return
    fi
    if [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null || true)" ]; then
        err "$dir exists and is not a pwnellij git checkout. Remove it or set PWNELLIJ_DIR elsewhere."
    fi
    if have git; then
        say "Cloning $REPO ($REF) into $dir"
        git clone --depth 1 --branch "$REF" "$REPO" "$dir" 2>/dev/null \
            || git clone "$REPO" "$dir"
    else
        download_tarball "$dir"
    fi
}

download_tarball() {
    dir="$1"
    url="${REPO%/}/archive/refs/heads/${REF}.tar.gz"
    say "git not found; downloading $url"
    tmp=$(mktemp -d)
    if have curl; then
        curl -sSfL "$url" -o "$tmp/pwnellij.tar.gz"
    elif have wget; then
        wget -qO "$tmp/pwnellij.tar.gz" "$url"
    else
        rm -rf "$tmp"
        err "need git, curl, or wget to download pwnellij"
    fi
    mkdir -p "$dir"
    tar -xzf "$tmp/pwnellij.tar.gz" -C "$tmp"
    inner=$(find "$tmp" -maxdepth 1 -type d -name 'pwnellij-*' | head -n1)
    [ -n "$inner" ] || { rm -rf "$tmp"; err "unexpected tarball layout from $url"; }
    cp -R "$inner/." "$dir/"
    rm -rf "$tmp"
}

link_bin() {
    name="$1"
    [ -f "$DIR/bin/$name" ] || err "$DIR/bin/$name not found — is $DIR a pwnellij checkout?"
    mkdir -p "$BIN_DIR"
    chmod +x "$DIR/bin/$name" 2>/dev/null || true
    ln -sf "$DIR/bin/$name" "$BIN_DIR/$name"
    say "Linked $BIN_DIR/$name -> $DIR/bin/$name"
}

layout_ctor() {
    # zellij is the default, so a bare Layout() selects it; tmux must be explicit
    # both to override that default and so bin/pwnellij detects the Tmux() call.
    if [ "$MUX" = tmux ]; then
        printf 'pwnellij.Layout(multiplexer=pwnellij.Tmux())'
    else
        printf 'pwnellij.Layout()'
    fi
}

render_block() {
    cat <<EOF

$MARK_START
# Managed by the pwnellij installer. Edit the layout below freely; delete this
# whole block (both markers) to remove pwnellij from your gdb config.
source $DIR/gdbinit.py
python
import pwnellij
($(layout_ctor)
 .tell_multiplexer(show_titles=True)
 .tell_multiplexer(set_title="Main")
   .above(display="disasm")
   .right(display="regs")
   .right(of="main", display="stack")
   .right(of="disasm", display="backtrace", size="30%")
   .show("legend", on="stack")
 ).build(nobanner=True)
end
$MARK_END
EOF
}

configure_gdbinit() {
    touch "$GDBINIT"
    if grep -qF "$MARK_START" "$GDBINIT" 2>/dev/null; then
        tmp=$(mktemp)
        # Blank lines are held back so the separator render_block emits above
        # the start marker is dropped with the block instead of stacking up on
        # every re-run.
        awk -v s="$MARK_START" -v e="$MARK_END" '
            $0 == s { skip = 1; nblank = 0 }
            skip    { if ($0 == e) skip = 0; next }
            /^$/    { nblank++; next }
            { while (nblank > 0) { print ""; nblank-- } print }
            END { while (nblank > 0) { print ""; nblank-- } }
        ' "$GDBINIT" > "$tmp"
        mv "$tmp" "$GDBINIT"
        verb="Updated"
    else
        verb="Added"
    fi
    render_block >> "$GDBINIT"
    say "$verb the pwnellij layout ($MUX) in $GDBINIT"
}

# Does the gdb that pwntools would launch have pwndbg? pwntools runs
# `pwntools-gdb` or `gdb` from $PATH, and a standalone pwndbg (one that ships
# its own gdb and python behind a `pwndbg` launcher) is invisible to that gdb --
# so every gdb.debug() would die in pwnellij's ~/.gdbinit block on `import
# pwndbg`.
gdb_imports_pwndbg() {
    have gdb || return 1
    # -nx on purpose: without it this probe runs the user's ~/.gdbinit, i.e.
    # builds a whole pwnellij layout -- spawning panes, if the installer is run
    # from inside a session -- to answer a yes/no question. The cost is that a
    # pwndbg loaded only by ~/.gdbinit reads as missing here; that is why the
    # caller also insists on finding a standalone `pwndbg` launcher before
    # touching anything.
    gdb -nx -batch -ex "python import pwndbg; print('PWNDBG_OK')" 2>/dev/null |
        grep -q PWNDBG_OK
}

configure_pwnconf() {
    pwndbg_bin=$(command -v pwndbg 2>/dev/null) || return 0
    if gdb_imports_pwndbg; then
        return 0
    fi

    if [ -f "$PWN_CONF" ] && grep -Eq '^[[:space:]]*gdb_binary[[:space:]]*=' "$PWN_CONF"; then
        say "$PWN_CONF already sets gdb_binary — leaving it alone"
        return 0
    fi

    # pwntools parses these values with pwnlib.util.safeeval, so the path has to
    # be a quoted Python string rather than a bare word.
    key="gdb_binary='$pwndbg_bin'"
    note="# pwnellij: pwntools launches plain gdb, which cannot import a standalone pwndbg."
    if [ ! -e "$PWN_CONF" ]; then
        printf '%s\n%s\n\n[context]\n%s\n' \
            "# Written by the pwnellij installer." "$note" "$key" > "$PWN_CONF"
    elif grep -Eq '^[[:space:]]*\[context\]' "$PWN_CONF"; then
        # A second [context] section is not an option: pwntools reads this file
        # with configparser in strict mode, where a duplicate section raises and
        # takes every `from pwn import *` down with it. Insert into the section
        # that is already there.
        tmp=$(mktemp)
        awk -v k="$key" -v n="$note" '
            !ins && /^[ \t]*\[context\]/ { print; print n; print k; ins = 1; next }
            { print }
        ' "$PWN_CONF" > "$tmp"
        mv "$tmp" "$PWN_CONF"
    else
        printf '\n%s\n[context]\n%s\n' "$note" "$key" >> "$PWN_CONF"
    fi
    say "Pointed pwntools at $pwndbg_bin in $PWN_CONF"
}

main() {
    case "$MUX" in
        tmux|zellij) ;;
        *) err "PWNELLIJ_MULTIPLEXER must be 'tmux' or 'zellij', got '$MUX'" ;;
    esac

    resolve_dir
    printf '%spwnellij installer%s\n' "$BOLD" "$RESET"
    say "checkout:    $DIR"
    say "wrapper:     $BIN_DIR/pwnellij"
    say "multiplexer: $MUX"

    fetch_repo "$DIR"
    link_bin pwnellij
    if [ -n "${PWNELLIJ_NO_GDBINIT:-}" ]; then
        say "Skipping ~/.gdbinit (PWNELLIJ_NO_GDBINIT set)"
    else
        configure_gdbinit
    fi
    # pwntools opens gdb in a terminal of its own choosing, which is a window
    # outside the session -- see bin/pwntools-terminal for the whole story.
    if [ -n "${PWNELLIJ_NO_PWNTOOLS:-}" ]; then
        say "Skipping the pwntools integration (PWNELLIJ_NO_PWNTOOLS set)"
    else
        link_bin pwntools-terminal
        configure_pwnconf
    fi

    # Runtime deps are the user's responsibility; just flag anything missing.
    have gdb || warn "gdb not found on PATH — pwnellij needs gdb with pwndbg."
    have "$MUX" || warn "$MUX not found on PATH — the wrapper launches gdb inside $MUX."

    case ":$PATH:" in
        *":$BIN_DIR:"*) : ;;
        *) warn "$BIN_DIR is not on your PATH. Add it, e.g.:
    export PATH=\"$BIN_DIR:\$PATH\"" ;;
    esac

    printf '\n%sInstalled.%s Start debugging with:\n\n    pwnellij ./your-binary\n\n' "$GREEN" "$RESET"
    printf 'This opens gdb+pwndbg inside %s and splits pwndbg context into panes\n' "$MUX"
    printf 'on the first run/start. Edit the layout in %s to taste.\n' "$GDBINIT"
}

main "$@"

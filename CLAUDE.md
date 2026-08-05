# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**Pwnellij** (module name: `pwnellij`) is a Python library for GDB that creates split-pane layouts (tmux or zellij) to display pwndbg debugging context (registers, stack, backtrace, assembly, etc.) in separate panes. It is loaded inside GDB via `gdbinit.py`.

## Installation / Usage

Source-only project. The core install path: clone the repo and add `source /path/to/pwnellij/gdbinit.py` to `~/.gdbinit`. `gdbinit.py` appends the checkout directory to `sys.path` so GDB's embedded Python finds the `pwnellij` package. There is intentionally **no** build backend, no `[project]` metadata, and no wheel — `pyproject.toml` only configures ruff and pytest.

`scripts/install.sh` automates that for the `curl … | sh` one-liner: it clones (or `git pull`s) into `${XDG_DATA_HOME:-~/.local/share}/pwnellij`, symlinks `bin/pwnellij` into `~/.local/bin`, and writes an idempotent, marker-delimited block into `~/.gdbinit` (the `source` line plus a default `Layout()…build()` layout — zellij by default, or the tmux variant when `PWNELLIJ_MULTIPLEXER=tmux`; the tmux variant is written as an explicit `Layout(multiplexer=pwnellij.Tmux())` so the wrapper detects it). It is POSIX `sh`, non-interactive (all config via env vars, since stdin is the pipe), and re-runnable. Run from inside an existing checkout (`sh scripts/install.sh`) it uses that checkout in place instead of cloning.

`bin/pwnellij` is a thin POSIX `sh` wrapper (like `scripts/install.sh`; `shellcheck -s sh` must pass) that launches `pwndbg` (gdb-with-pwndbg) inside a multiplexer, sidestepping the "GDB is already attached to a non-multiplexer TTY" problem by making sure GDB starts inside one from the outset. It picks the backend from `PWNELLIJ_MULTIPLEXER` if set, otherwise greps `~/.gdbinit` and `./.gdbinit` for a non-commented `Tmux(` call (zellij is the default). For tmux it runs `tmux new-window` when already in tmux, else `tmux new-session -s pwnellij` (falling back to an auto-named session when a `pwnellij` session already exists, instead of dying with "duplicate session"). For zellij, inside an existing session it runs `zellij action new-tab --close-on-exit -- bash -lc …`; `--close-on-exit` closes the gdb pane when pwndbg exits (zellij otherwise holds the dead command pane open), so the tab closes and returns you to your previous tab, matching tmux's `new-window`. Outside a session it starts a fresh one via `zellij --layout-string` (mirroring tmux's `new-session`), passing the pwndbg command through the `PWNELLIJ_CMD` env var so no KDL escaping is needed, with `close_on_exit true` so the session ends and drops back to the shell when pwndbg exits. In all four paths the zellij tab / tmux window is titled `pwnellij: <binary>` — via `new-tab --name`, a `tab name=…` node in the layout (the title *is* interpolated into the KDL there, so the wrapper escapes `\` and `"` in it), or tmux's `-n` (which also disables `automatic-rename` for that window, so the title sticks). The title comes from a single scan over the arguments that works out what is being debugged; the arguments themselves are always forwarded to pwndbg untouched. Both multiplexers take that command as a *single shell-command string*, so every argument goes through a small `quote()` helper first: shlex-style single-quoting, applied only to words containing something outside a safe character set so the common command line stays readable. bash's `printf '%q'` is deliberately not used — tmux re-parses the string with the user's `default-shell`, which need not be bash and need not understand the `$'…'` form `%q` emits for control characters. `tests/test_wrapper.py` pins this by round-tripping a set of hostile arguments back through `sh`, `bash` and `zsh`. The scan skips over the value of gdb options that take a *separate* argument (`-x`, `-ex`, `-c`, `-d`, …) so a filename or a gdb command is never mistaken for the binary, and recognizes gdb's attach flag in every spelling gdb accepts (`-p N`, `-pN`, `-pid N`, `--pid N`, `--pid=N`, `-pid=N`). `--args` (and gdb's single-dash `-args`) ends the scan: its value is the binary and everything after it belongs to the debuggee, so a program's own `-p`/`--pid` argument is never read as gdb's attach flag — which used to abort the launch outright when that value named no live process. `-h`/`--help` (both spellings gdb itself accepts) is intercepted inside that same scan and prints the wrapper's own `usage()` — the skip list and the `--args` break therefore apply to it too, so a `--help` that is an `-ex` value or a debuggee argument is passed through untouched. It is handled there rather than up front so it short-circuits the pid checks: asking for help never fails and never opens a tab (gdb's `--help` would otherwise print into a tab that closes as it exits). With a pid the title becomes `pwnellij: <comm> (<pid>)` from `ps -o comm=`, falling back to `pwnellij: pid <pid>` when `ps` is unavailable; otherwise it is the basename of the first non-option argument, or plain `pwnellij` if there is none. A pid that is non-numeric or names no live process is a hard error before any window is opened — otherwise the failure would land in a freshly-opened tab the user has to switch to. **Attaching needs nothing from the library side:** gdb sources `~/.gdbinit` (building the layout) before it attaches, so pwndbg's context lands in the panes on the attach stop. Setting `PWNELLIJ_DRY_RUN=1` makes the wrapper print the resolved `mux=`/`title=`/`cmd=` and exit instead of launching — that is how `tests/test_wrapper.py` exercises the argument scan without a real session. It also strips the other multiplexer's env vars (`TMUX`/`TMUX_PANE` vs `ZELLIJ*`) so the inner gdb's `Tmux()`/`Zellij()` detection isn't fooled. Lives in `bin/` (not the repo root) to avoid colliding with the `pwnellij/` Python package directory; users typically symlink it into `~/.local/bin/pwnellij` (the installer does this automatically).

Then configure a layout in their `.gdbinit`:
```python
import pwnellij
(pwnellij.Layout()
  .above(display="disasm", size="75%")
  .left(display="regs", size="35%")
  .show("stack")
  .build()
)
```

## Architecture

Three loosely coupled layers. Contracts live in `pwnellij/models.py`:

- `Split` — a `NamedTuple` of `(id, tty, display, settings)`. Pure data, no backend behavior.
- `Multiplexer` (ABC) — layout backend. Required methods: `left/right/above/below`, `show`, `get`, `splits`, `size`, `finish`, `do`, `close`.
- `Debugger` (ABC) — content producer. Required method: `setup(multiplexer, **kwargs)`.

**`Layout`** (`pwnellij/layout.py`) — fluent builder. Accumulates layout calls then `build()` triggers the multiplexer and debugger. The directional methods (`left/right/above/below`) all funnel through `_wrap`; add new ones the same way. Multiplexer selection is explicit via `Layout(multiplexer=...)` — defaults to `Zellij()`, no env-based auto-detection. The default `Zellij()` raises `RuntimeError` outside zellij (and `Tmux()` likewise outside tmux). **Graceful fallback:** when `Layout` constructs the *default* multiplexer and that construction raises (e.g. GDB launched outside zellij, as happens with `pwn.gdb.debug` when not inside a zellij session), `Layout` catches it, prints a `pwnellij: failed to load …` warning to stderr, and substitutes `_FallbackMultiplexer` — a no-op backend so the fluent chain still completes. In that mode `build()` returns early without wiring the debugger, leaving pwndbg to render its context inline. This only covers the default path; an explicitly passed `Layout(multiplexer=Zellij())` raises before `Layout` can catch it.

**`Tmux`** (`pwnellij/multiplexer/tmux.py`) — tmux implementation of `Multiplexer`. All tmux interaction routes through `TmuxBackend.run()`, which is injectable (`Tmux(backend=...)`) for tests/fakes. Pane sizing is queried via `multiplexer.size(split)`, not from the `Split` itself. `Tmux(mouse=True)` (the default) turns on tmux's session `mouse` option so the wheel scrolls each pane's copy-mode history (the only way to review output that scrolled out of a pane); the prior value is saved and restored on `close()`. Pass `Tmux(mouse=False)` to leave the user's mouse setting untouched (keyboard copy-mode, `prefix [`, still scrolls).

**`Zellij`** (`pwnellij/multiplexer/zellij.py`) — zellij implementation of `Multiplexer`. Same backend-injection pattern as `Tmux`. Zellij does not expose pane TTYs via the CLI, so each new pane is spawned with a small bash helper that writes its tty and `stty size` to a sentinel file under `tempfile.mkdtemp(prefix="pwnellij-zellij-")`; `ZellijBackend.read_sentinel` polls the file with a timeout. Afterwards sizes are measured **live** through that tty (`ZellijBackend.tty_size`, an ioctl), so `size(split)` tracks terminal resizes; the sentinel value is only a fallback. zellij's `new-pane` (as of 0.44) cannot target a pane and only implements the `Right`/`Down` directions (`Left`/`Up` are accepted but silently treated as `Right`/`Down`), so the rest is emulated: `of=` targeting cycles `focus-next-pane` until `list-clients` reports the target focused (no target means the main/gdb pane, mirroring tmux); `left`/`above` split `Right`/`Down` then swap the two panes with `move-pane --pane-id`; `size=` nudges the new pane toward the requested size with `resize` steps, re-measuring via the tty each step (best effort — zellij resizes in coarse ~5% increments). `finish()` refocuses the main pane; `close()` closes created panes via `close-pane --pane-id`. `do(show_titles=...)` is a no-op (zellij always renders pane names when set).

**`Pwndbg`** (`pwnellij/debugger/pwndbg.py`) — pwndbg implementation of `Debugger`. Imports pwndbg (and `gdb`) lazily inside `setup()` so the module imports cleanly outside GDB; a clear `RuntimeError` is raised if pwndbg is missing when setup runs. `setup()` also honors `inferior=True` on a split: it runs `gdb.execute("set inferior-tty <pane tty>")` so the debugged program's own stdin/stdout land in that pane rather than the main/gdb pane. This is set during `build()`, before the first `run`, which is when gdb applies it. `Layout._wrap` fills the sensible defaults for such a split — an idle `cmd="tail -f /dev/null"` (idles without reading stdin, so it doesn't steal the program's input) and `clearing=False` (so program output isn't wiped) — unless the caller overrides them; the `inferior` flag rides through the multiplexer into the split's `settings` where the debugger reads it.

Data flow: `Layout.build()` → `Multiplexer.finish()` → `Debugger.setup(multiplexer)` → debugger iterates `multiplexer.splits()`, queries `multiplexer.size(pane)` for layout, writes to each pane's TTY.

## Linting / Formatting

`ruff` handles both, configured in `pyproject.toml` (target `py310`, 100-col, rules `E W F I B UP SIM RUF`). Install once with `pipx install ruff`, then run:

```
ruff check .       # lint
ruff format .      # auto-format
```

Both must pass clean before committing. The two shell scripts (`bin/pwnellij`, `scripts/install.sh`) are checked with `shellcheck` instead — both are POSIX `sh`, so run it in that dialect:

```
shellcheck -s sh bin/pwnellij scripts/install.sh
```

Keep them bashism-free (no `[[ ]]`, arrays, `+=`, `<<<`, `${v//x/y}`, `printf %q`, `local`, `set -o pipefail`); shellcheck's SC3xxx codes catch these. `gdbinit.py` has `# noqa: E402, F401` on the `import pwnellij` line — that import is deliberate (it runs after `sys.path` is rewritten and its only purpose is the side-effect load), do not remove the noqa.

## Tests

`pytest` from the repo root runs the suite in `tests/`. Config under `[tool.pytest.ini_options]` sets `pythonpath = ["."]` so the suite works against the source tree without any install. Install once with `pipx install pytest`, then `pytest`. Run a single test with `pytest tests/test_tmux.py::test_split_appends_size_flag`.

`tests/test_wrapper.py` covers the `bin/pwnellij` launcher by running it under `PWNELLIJ_DRY_RUN=1` and parsing its `mux=`/`title=`/`cmd=` output — no tmux, zellij or gdb needed. The `Tmux` and `Zellij` multiplexers are each exercised via a fake backend that records every `run()` call (`FakeBackend` in `tests/test_tmux.py`, `FakeZellijBackend` in `tests/test_zellij.py`) — when adding backend behavior, extend the relevant fake rather than reaching for a real tmux/zellij session. `Layout` is tested against a `FakeMultiplexer`/`FakeDebugger` pair. `tests/test_installer.py` runs `scripts/install.sh` for real, but only ever against the checkout it lives in and with `PWNELLIJ_BIN_DIR`/`GDBINIT` pointed inside `tmp_path`, so it needs no network and writes nothing outside the temporary directory — keep any new installer test on that footing. Manual end-to-end testing still requires GDB + pwndbg + tmux (or zellij).

CI lives in `.github/workflows/ci.yml` and runs `ruff check`, `ruff format --check`, `shellcheck -s sh`, and `pytest` on every push to `main` and on PRs.

## Changelog

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and SemVer, starting at 1.0.0. Anything a user would notice — API additions, wrapper or installer behavior, fixed bugs — gets a one-line entry under `## [Unreleased]` in the same commit that makes the change; refactors, test-only work and doc tweaks do not. Releasing means renaming that section to `## [x.y.z] - YYYY-MM-DD`, adding a fresh empty `## [Unreleased]`, updating the two link definitions at the bottom of the file, and tagging the commit `vx.y.z`.

## Adding a New Multiplexer or Debugger

- Subclass `Multiplexer` or `Debugger` from `pwnellij.models`. The ABC will reject construction if any abstract method is missing.
- New multiplexers go in `pwnellij/multiplexer/`, new debuggers go in `pwnellij/debugger/`.
- Export from `pwnellij/__init__.py`.

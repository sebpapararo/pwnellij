# Changelog

All notable changes to pwnellij are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
User-facing changes go under **Unreleased** as they land; that section is
renamed to the version on release.

## [Unreleased]

### Added

- **pwntools integration.** `bin/pwntools-terminal` puts the gdb that
  `gdb.debug()`/`gdb.attach()` launches into a new tab of the zellij session (or
  a new tmux window) the exploit is already running in, instead of the detached
  terminal window pwntools would otherwise pick — which left the gdb prompt and
  pwnellij's context panes in two different places. The installer symlinks it
  alongside `pwnellij`, and, when the gdb on `$PATH` cannot import pwndbg but a
  standalone `pwndbg` launcher is installed, points pwntools at that launcher
  with `gdb_binary` in `~/.pwn.conf`. `PWNELLIJ_NO_PWNTOOLS=1` skips both.

### Fixed

- A failing `build()` no longer aborts with a raw Python exception. If the
  multiplexer or the debugger blows up while the layout is being built — most
  often a gdb without pwndbg, as when pwntools' `gdb.debug()` launches the
  system gdb — pwnellij now warns, closes the panes it opened, and lets the
  debugger print its context inline, matching the existing fallback for a
  multiplexer that cannot start.

## [1.0.0] - 2026-08-05

First tagged release. pwnellij began as a fork of
[splitmind](https://github.com/jerdna-regeiz/splitmind) by jerdna-regeiz and has
since gained a zellij backend, a launcher, an installer and a test suite.

### Added

- **Layout API.** `pwnellij.Layout` is a fluent builder — `left`/`right`/`above`/
  `below` create a pane relative to the last one, `of=` targets an earlier
  split by name, `show` routes an extra section into an existing pane, and
  `build()` hands the finished layout to the debugger. Checkouts from before
  this release called the builder `Mind`; re-run the installer, or rename the
  call in your `~/.gdbinit`, if gdb reports no attribute `Mind`.
- **zellij support**, the default backend. Pane TTYs are discovered through a
  sentinel file and sizes measured live, so layouts follow terminal resizes;
  pane targeting, `Left`/`Up` splits and `size=` are emulated on top of what
  zellij's CLI actually offers.
- **tmux support** via `Layout(multiplexer=pwnellij.Tmux())`, including pane
  titles and mouse mode enabled by default so each pane's history stays
  scrollable. The previous mouse setting is restored on exit.
- **pwndbg integration.** Context sections (registers, disassembly, stack,
  backtrace, …) are written to the TTY of the pane bound to each one.
  `inferior=True` gives the debugged program its own pane for stdin/stdout.
- **`pwnellij` launcher.** Starts gdb+pwndbg inside zellij or tmux — a new tab
  or window when already in a session, a fresh session otherwise — so gdb never
  has to attach to a non-multiplexer TTY. The tab is named after the binary, or
  after the process when attaching. `--pid` is understood in every spelling gdb
  accepts, and a pid that names no live process fails before a tab is opened.
  `-h`/`--help` prints the wrapper's own usage; `PWNELLIJ_MULTIPLEXER` picks the
  backend and `PWNELLIJ_DRY_RUN` reports what would be launched.
- **One-command installer.** `scripts/install.sh` clones or updates the
  checkout, links the launcher onto `PATH`, and writes a marker-delimited,
  re-runnable layout block into `~/.gdbinit`. All options are environment
  variables, so it works through `curl … | sh`.
- **Graceful fallback.** When the default multiplexer cannot be constructed —
  gdb launched outside zellij, as with `pwn.gdb.debug` — pwnellij warns and
  leaves pwndbg to render inline instead of failing the layout.
- **Tests and CI.** `pytest` covers the multiplexers through fake backends and
  the shell scripts end to end without needing gdb, tmux or zellij; GitHub
  Actions runs ruff, shellcheck and the suite on every push and pull request.

[Unreleased]: https://github.com/sebpapararo/pwnellij/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/sebpapararo/pwnellij/releases/tag/v1.0.0

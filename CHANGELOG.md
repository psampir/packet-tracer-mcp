# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-10-27

Prompt-aware CLI modes, device readiness and dry runs. **The Packet Tracer side
must be updated too**: replace the Script Engine file with the new
`bridge/bridge.js` and Stop → Start the module (see
[docs/setup.md §6](docs/setup.md#6-updating-the-bridge-later)).

### Added

- **Readiness (`wait_ready`, PTM-01).** `pt_configure` and `pt_show` wait — in
  real time, polling `get_state` every 250 ms — until a device shows a usable
  prompt (`>`, `#`, `(config…)#`, `C:\>`) before typing anything, bounded by
  `ready_timeout` (default 45 s). `pt_add_device` now answers
  `{"name": …, "ready": false}` while the device boots, and
  `pt_power(..., ready=True)` waits after powering on. A freshly added 2911 or
  2960 no longer eats the first configuration as wizard answers or `% Invalid
  input`.
- **`dry_run` for `pt_configure` (PTM-16).** Returns the exact line sequence the
  bridge would send — wrappers included — without sending or waiting, via the
  new bridge `plan` op: `{"dry_run": true, "lines": [...], "mode": "auto",
  "effective_mode": "config"}`.
- **New bridge ops** `get_state`, `plan`, `configure`, `exec` (and `show` kept as
  an `exec` alias): per-device prompt/mode/boot state, planning, and line-by-line
  execution with per-line results. The wire format is unchanged.
- **Per-line results (PTM-06).** Every `pt_configure`/`pt_show` result now carries
  `lines: [{line, prompt, output, ok}]` with each line's own output delta, plus
  `stopped_on: {index, line, prompt, reason, output}` — a **1-based** index into
  the injected sequence — and `failed: [index]` when `stop_on_error=false`.
- **[docs/cli-semantics.md](docs/cli-semantics.md)**: the mode table
  (`auto|raw|exec|config`), prefix/suffix rules, prompt detection, the per-line
  wait and error format, readiness, `dry_run`, `keep_session`, `stop_on_error`,
  and the "a PC/shell gets zero insertions" guarantee.
- **Node harness models a CLI**: mock devices now have a prompt state machine
  (`>` → `#` → `(config)#` → `(config-if)#`, `end`, `exit`), per-type IOS-ness,
  PCs starting at `C:\>`, a rejectable line and a boot delay. 72 Node tests
  (was 41).
- **PTM-01/02/06/16 test coverage**: `tests/test_ready.py` (readiness retry,
  timeout message, `pt_power(ready=True)`), rewritten `tests/test_commands.py`
  and a new `tests/bridge/cli.test.js`.

### Changed

- **BREAKING (0.2.0): `pt_link(a, a_port, b, b_port, cable=…)` and
  `pt_autolink(a, b)`.** The old `dev_a/port_a/dev_b/port_b` names are gone so
  both ends follow one convention (PTM-16).
- **`normalize_commands()` is split + strip only (PTM-02).** All
  `enable`/`configure terminal`/`end` policy moved into `bridge/bridge.js`,
  which is the only side that can see the prompt. Devices without IOS
  (`ePc`, `eServer`, `ePrinter`, `eLaptop`, access points, phones, IoT) always
  get the lines verbatim.
- **`pt_configure` signature**: `mode="auto"`, `keep_session=False`,
  `stop_on_error=True`, `dry_run=False`, `ready_timeout=45`; `pt_show` gained
  `ready_timeout`; `pt_power` gained `ready`/`ready_timeout`.
- **Architecture, protocol and setup docs** describe the new ops, the wait and
  the Script Module update procedure; README tool table updated.
- Version bumped to `0.2.0` (`pyproject.toml`, `pt_mcp.__version__`, `uv.lock`).

### Fixed

- **PTM-02 — no more unconditional wrappers.** A PC running
  `pt_configure(["ping …"])` sends exactly one line (no `Invalid Command.` ahead
  of it); a switch at `Switch#` running `show vlan brief` stays in exec mode (no
  DNS-lookup lockup); `end` is only ever sent from config mode; `enable` only from
  user exec.
- **PTM-06 — commands are no longer batched.** Each line waits for its prompt
  change (or 120 ms of a stable buffer, capped by `timeout_ms`) before the next
  one, so a 30-line block lands in order, and a rejected or stuck line is
  reported with its index, text and prompt instead of being skipped.
- **PTM-01 — boot-time configuration corruption** (wizard answers, `% Invalid
  input`) is prevented by waiting for the prompt before sending.
- **PTM-16 — no more guessing the name convention** for links, and a mode bug can
  be caught with `dry_run` before it costs a console.

### Previously unreleased (now part of 0.2.0)

- **Architecture doc** ([docs/architecture.md](docs/architecture.md)): why the
  transport is a directory of JSON files (no sockets, no wall-clock timers in the
  PT Script Engine, `SystemFileManager` as the only host bridge), the component
  layout and end-to-end flow as Mermaid diagrams, execution semantics, the
  security and privilege model and a failure-mode table.
- **Tag-driven release workflow** (`.github/workflows/release.yml`).
- **Node test harness for the PT-side bridge** (`tests/bridge/`): loads
  `bridge/bridge.js` **unmodified** into a `node:vm` context.
- **Python coverage gate** (`pytest-cov`, `fail_under = 85`); the suite is at
  100% line coverage of `pt_mcp`.

## [0.1.0] - 2026-10-27

### Added

- **File-drop bridge between the MCP server and Packet Tracer.** A job is written atomically
  (`.json.tmp` → `os.replace`) into `<PT_MCP_HOME>/jobs`, `bridge/bridge.js` performs the op inside
  Packet Tracer's Script Module engine, and the result lands in `<PT_MCP_HOME>/results`. No sockets,
  no ports, no credentials; the bridge always acts on the **active** `.pkt`.
- **Eleven MCP tools over stdio** (`packet-tracer-mcp`): `pt_status`, `pt_list_devices`,
  `pt_add_device`, `pt_remove_device`, `pt_link`, `pt_autolink`, `pt_configure`, `pt_show`,
  `pt_power`, `pt_save`, `pt_raw`. Every failure becomes `{"ok": false, "error": …}` instead of a
  traceback.
- **`pt_configure` command normalization**: accepts a newline string or a list, drops blanks and
  `!` comments, prepends `enable` + `configure terminal` when absent, appends `end`.
- **Robust transport**: 50 ms polling with retry on partially written results, stale-response
  detection by request id, response cleanup after every read, and job cleanup on timeout so a late
  bridge cannot execute an abandoned command. Timeouts explain the PT-side causes (PT not running, no
  active `.pkt`, module not started, watcher silent, missing privileges).
- **ES5-only script module** (`bridge/bridge.js`, QtScript-safe) covering devices, links, CLI,
  power, save and a deliberate `eval` escape hatch, with a file watcher plus an explicit rescan path
  for builds where watcher events don't fire.
- **Offline test suite** (pytest) driven by `tests/fake_bridge.py`, a stand-in that speaks the same
  protocol; no Packet Tracer and no network needed.
- **Docs**: click-by-click Script Module installation with security privileges
  ([docs/setup.md](docs/setup.md)), the wire protocol and cleanup semantics
  ([docs/protocol.md](docs/protocol.md)), an opencode client snippet
  ([examples/opencode-snippet.jsonc](examples/opencode-snippet.jsonc)).
- **Tooling**: hatchling package layout, `uv` lockfile, ruff (100 cols, py312) and a GitHub Actions
  workflow running lint + tests.

[0.2.0]: https://github.com/psampir/packet-tracer-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/psampir/packet-tracer-mcp/releases/tag/v0.1.0

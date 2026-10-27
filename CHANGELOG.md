# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Architecture doc** ([docs/architecture.md](docs/architecture.md)): why the transport is a
  directory of JSON files (no sockets, no wall-clock timers in the PT Script Engine, `SystemFileManager`
  as the only host bridge), the component layout and end-to-end flow as Mermaid diagrams, the
  `pt_configure` call sequence with its atomic-write/dedupe/mode handling, execution semantics
  (active `.pkt` only, serial jobs, `Run File` recovery, server-side timeout cleanup), the security
  and privilege model including `pt_raw` as an escape hatch, and a failure-mode table.
- **Tag-driven release workflow** (`.github/workflows/release.yml`): a `v*` tag verifies that the tag
  equals `project.version` in `pyproject.toml`, runs ruff + pytest, then `uv build` and publishes the
  wheel and sdist to a GitHub Release with generated notes.
- **Node test harness for the PT-side bridge** (`tests/bridge/`): `harness.js` loads
  `bridge/bridge.js` **unmodified** into a `node:vm` context and fakes the host it runs against inside
  Packet Tracer — `ipc.appWindow()`/`network()`/`systemFileManager()` (an in-memory directory + file
  map), mock devices whose `ConsoleLine` has `getOutput()` and whose `TerminalLine` deliberately does
  not, and a recording file watcher. 41 `node:test` cases cover the pure helpers (`resolveDeviceType`,
  `resolveCable`, `baseName`, `tail`, `safeId`, `safeStringify`), every dispatch op, the
  job → result file transport (dedupe via `PT_MCP_SEEN`, invalid JSON, unknown device, several jobs per
  scan, the `writePlainTextToFile` fallback) and `main()`/watcher startup. Node built-ins only: no npm
  dependencies and no Packet Tracer needed.
- **Python coverage gate** (`pytest-cov`): `[tool.coverage.run]`/`[tool.coverage.report]` in
  `pyproject.toml` with `fail_under = 85`, plus tests for the previously uncovered timeout-parsing and
  retry paths (partially written result, mismatched id, missing mailbox directory) — 100% line
  coverage of `pt_mcp`.

### Changed

- **README**: new *Architecture* and *Releasing* sections pointing at the design doc, the wire
  protocol, the PT setup guide and the tag/re-tag procedure; the *Development* section now lists the
  coverage command and the Node suite and points at `tests/bridge/`.
- **CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) now runs the Node bridge suite after
  lint and pytest under an 85% coverage gate (`--cov=pt_mcp --cov-fail-under=85`), and `uv.lock` is
  refreshed for `pytest-cov` so `uv sync --frozen` keeps working.

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
- **Offline test suite** (pytest, 44 tests) driven by `tests/fake_bridge.py`, a stand-in that speaks
  the same protocol; no Packet Tracer and no network needed.
- **Docs**: click-by-click Script Module installation with security privileges
  ([docs/setup.md](docs/setup.md)), the wire protocol and cleanup semantics
  ([docs/protocol.md](docs/protocol.md)), an opencode client snippet
  ([examples/opencode-snippet.jsonc](examples/opencode-snippet.jsonc)).
- **Tooling**: hatchling package layout, `uv` lockfile, ruff (100 cols, py312) and a GitHub Actions
  workflow running lint + tests.

[0.1.0]: https://github.com/psampir/packet-tracer-mcp/releases/tag/v0.1.0

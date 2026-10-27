# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

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

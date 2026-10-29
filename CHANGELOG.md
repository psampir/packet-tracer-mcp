# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-10-29

The `pt_` / `pt-mcp` shorthand is gone from the public surface: tools are called
`status`, `configure`, `raw`, … and the Packet Tracer Script Module is named
`packet-tracer-mcp` with the ID `com.psampir.packet-tracer-mcp`. Nothing changes
on the wire — `bridge/bridge.js` is untouched, so no re-paste is needed.

### Changed

- **Tool names lost the `pt_` prefix.** All 22 MCP tools are now `status`,
  `list_devices`, `show`, `add_device`, `remove_device`, `link`, `autolink`,
  `power`, `skip_boot`, `list_links`, `list_ports`, `get_config`, `set_ip`,
  `set_ipv6`, `dns_record`, `dns_list`, `http_page`, `pc_command`, `ping`,
  `configure`, `save`, `raw`. Error messages, generated snippet markers
  (`/* packet-tracer-mcp: <tool> */`) and docstrings follow.
- **The PT Script Module is `packet-tracer-mcp`** (Name) with the ID
  `com.psampir.packet-tracer-mcp`; `ptmcp.pts` becomes
  `packet-tracer-mcp.pts`. Existing installs rename it once in
  *Configure PT Script Modules…* and quit with `File → Exit` — see
  [docs/setup.md §7](docs/setup.md#renaming-the-module-030).
- **The opencode MCP server key is `packet-tracer`** instead of `pt`
  (`examples/opencode-snippet.jsonc`, `~/.config/opencode/opencode.jsonc`).
- The runtime mailbox keeps its old spelling: `PT_MCP_HOME`, default
  `~/.local/share/pt-mcp`, and the Python package stays `pt_mcp`.
- Internal: `server.py` split into registry/readiness/tables/eval/snippets/hosts/
  topology/raw + `tools/*`; the 22 tool names and behaviour are unchanged.

### Breaking

- **Every tool call must use the new names.** Clients that call `pt_status`,
  `pt_configure`, `pt_raw`, … get "unknown tool"; switch to `status`,
  `configure`, `raw`, … . The renamed tool names also change the
  `packet_tracer_*` prefix an MCP client exposes, so reconnect/reload the server
  after upgrading.

### Added

- **`tools/live_smoke.py`** — a live integration smoke test that builds a
  throw-away lab in the active `.pkt`, exercises the real IPC paths (boot wizard,
  DHCP, IPv6, DNS/HTTP, routing, CLI errors, power/skip_boot) and removes every
  device afterwards. 16 checks; not part of CI.

### Fixed

- **`dns_list()` without a name returned `[object Object]` for every entry.**
  Entries are now `{"name": ..., "ip": ...}` instead of an opaque pair.

## [0.2.2] - 2026-10-28

### Fixed

- **`pt_dns_record(enable=False)` passes the address to
  `removeARecordFromNameServerDb(domain, ip)`.** The one-argument call made PT
  reject the IPC call and took the engine down; removal now mirrors the add
  path.
- **IPv6 addresses are formatted.** `pt_get_config` / `pt_set_ipv6` returned
  `[object Object]`; entries are now rendered as `address/prefix`.
- **`pt_http_page(enable=False)` no longer queries the disabled server.**
  `isEnabled()` / `getPage()` after `setEnable(false)` took Packet Tracer down;
  the tool now returns `enabled: false` without touching the process.

## [0.2.1] - 2026-10-28

### Fixed

- **`pt_raw` keeps the value's type.** The wrapper ran `eval()` on the
  expression's *value*, so a string result was re-evaluated as code (`"ok"` →
  `ReferenceError: ok is not defined`). The source is now embedded as a JSON
  string literal, so `"ok"` stays `"ok"` and multi-line snippets still work.
- **`pt_dns_record` enables the DNS service.** A Server-PT ships with the DNS
  process disabled; adding records alone left `nslookup` timing out. The tool
  now calls `DnsServerProcess.setEnable(true)` and reports the real `enabled`
  state.

## [0.2.0] - 2026-10-27

Prompt-aware CLI modes, device readiness and dry runs. **The Packet Tracer side
must be updated too**: replace the Script Engine file with the new
`bridge/bridge.js` and Stop → Start the module (see
[docs/setup.md §7](docs/setup.md#7-updating-the-bridge-later)).

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
- **Host configuration tools, all server-side over the existing `eval` op
  (PTM-03/04) — no bridge change, no Script Module re-paste.** Each generates an
  ES5 snippet tagged `/* pt-mcp: <tool> */` and unwraps the double-encoded
  `value`:
  - `pt_get_config(device, port=None)` → `{"ports": [{name, ip, mask, dhcp,
    ipv6, up}]}`;
  - `pt_set_ip(device, port="FastEthernet0", ip, mask, gateway, dns,
    dhcp=False)` → static `setIpSubnetMask(ip, mask)` (address first), or
    `setDhcpClientFlag(true)` + `DhcpClient.dhcpRun(port)`; returns the read-back
    `{ip, mask, dhcp}`;
  - `pt_set_ipv6(device, port, address, prefix=64, gateway=None)` →
    `setIpv6Enabled(true)` + `addIpv6Address(addr, prefix, 0, false)` +
    optional `setv6DefaultGateway`;
  - `pt_dns_record(device, name, ip, enable=True, zone, ns_name, soa=True,
    ns=True, mailbox, min_ttl, refresh, retry, expiry)` → **both**
    `addIpAddress` and `addARecordToNameServerDb` in one call, plus the zone's
    SOA and NS, so the answer is neither NXDOMAIN nor non-authoritative;
    `enable=False` removes both halves; a two-label name is its own zone, a
    longer one drops the first label;
  - `pt_dns_list(device, name=None)`, `pt_http_page(device, path, content,
    enable)`;
  - `pt_pc_command(device, command, wait=10)` returns the *whole* new console
    tail (buffer length taken before the command, read again after `wait`), and
    `pt_ping(src, dst, count=4, wait=10)` parses replies, timeouts, unreachable
    answers and PT's `Sent/Received/Lost … % loss` line (`count` is advisory:
    PT's PC ping always sends four).
- **Topology introspection (PTM-12).** `pt_list_links()` walks
  `Port.getLink()` → `Cable.getPort1/getPort2` and returns
  `[{a, a_port, b, b_port, cable, up}]`, deduped in JS *and* in Python so a cable
  is one entry whichever end was walked first; `pt_list_ports(device)` returns
  `[{name, up, protocol_up, ip, mask, link}]` with the peer of a connected port.
- **`pt_skip_boot(device, wait=False, ready_timeout=45)` (PTM-08)** exposes the
  bridge's existing `skip_boot` op, and `pt_add_device(..., skip_boot=True)` does
  add + skip in one call.
- **`pt_raw(js, max_chars=20000)` hardened (PTM-15).** The script is wrapped in
  try/catch, so a thrown error returns the message and the JS line number as
  `PT API error: … at line N` instead of a C++ exception; a serialisable result
  comes back as a **real JSON value** (no second parse on the client); results
  longer than `max_chars` are cut with `"truncated": true` and `chars`.
- **Discoverability for bad names (PTM-09/10).** The server validates
  `pt_add_device(type=…)` against the `DeviceType` table and `pt_link(cable=…)`
  against the cable table *before* the call: names are accepted with or without
  the leading `e` and case-insensitively, and an unknown one returns
  `unknown device type 'X'; valid: …` plus a `difflib` "Did you mean eRouter?"
  hint. Nothing invalid is passed through to Packet Tracer.
- **[docs/screenshot.md](docs/screenshot.md) (PTM-13)**: the working Linux/Wayland
  recipe — find the PT window with `niri msg -j windows`, focus it, capture with
  `grim` (fullscreen-output trick or `slurp` region), crop with ImageMagick or
  Pillow — plus what to shoot for a lab report and the manual/`browser.preview`
  fallbacks. PT 9.0.1 exposes no render-to-image API, so `pt_export_png` stays
  unimplemented by design.
- **Recovery section in [docs/setup.md §6](docs/setup.md#6-recovery-a-wedged-console)
  (PTM-07)**: the ordered way out of a wedged console — wait for the prompt →
  `pt_power(off)` + `pt_power(on, ready=True)` → `pt_skip_boot` → restart PT →
  re-paste the bridge — with the warning that a power cycle discards everything
  not saved in the `.pkt`. Linked from the README troubleshooting table.
- **`tests/test_hosts.py`**: 100+ cases for the new tools (static and DHCP
  addressing, IPv6, both DNS A calls plus SOA/NS and the removal path, DNS
  listing, HTTP pages, the PC command buffer, ping parsing incl. unreachable,
  link dedupe, port inventory, `pt_raw` value/truncation/error+line,
  device-type and cable suggestions), plus end-to-end runs through
  `tests/fake_bridge.py`, whose `eval` now answers the marker-tagged snippets.
  The suite is 197 Python tests; coverage stays at 100%.

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
- **Twenty-two tools, not eleven.** The README table, `docs/protocol.md` (a new
  "Server-side helpers over `eval`" section and the `skip_boot`/`eval` rows) and
  `docs/architecture.md` (the host/topology sequence diagram and the two ways to
  extend the server) describe the new surface; `docs/cli-semantics.md` notes that
  hosts are configured through the API tools, not through the CLI.
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
- **Initial-configuration dialog.** A fresh IOS router that parks at
  `Would you like to enter the initial configuration dialog? [yes/no]:` is now
  answered once with `no` **and one RETURN** (`Press RETURN to get started!`) by
  `wait_ready`, instead of waiting 45 s and failing; if the dialog does not
  accept it, the error names it and suggests *Skip boot* or a power-cycle.
- **PTM-16 — no more guessing the name convention** for links, and a mode bug can
  be caught with `dry_run` before it costs a console.
- **PTM-03 — a whole lab no longer needs `pt_raw`.** Server addressing, DHCP,
  DNS records, HTTP pages and connectivity tests are first-class tools.
- **PTM-04 — DNS answers instead of NXDOMAIN.** `pt_dns_record` always writes the
  name-server DB record together with the address, and adds SOA/NS so the reply
  is authoritative.
- **PTM-09/10 — errors that name the alternative** for `type` and `cable`,
  instead of a bare "unknown device type".
- **PTM-11 — PT API failures are labelled.** Every `eval`-based tool raises
  `PT API error: … (op=…, device=…)`, never a raw engine exception.
- **PTM-15 — `pt_raw` is usable in anger**: a JSON value instead of a string, a
  line number on failure, and a size cap.

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

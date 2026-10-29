# packet-tracer-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI agent **build and configure a live
Cisco Packet Tracer topology**: add devices, cable them, push IOS CLI, read `show` output, all through
Packet Tracer's own Script Module API, with no network port and no PT plugin to compile.

[![CI](https://github.com/psampir/packet-tracer-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/psampir/packet-tracer-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **Unofficial.** Not affiliated with or endorsed by Cisco. It drives *your own* Packet Tracer
> install through the documented Scripting/Extensions API and ships no Cisco code:
> see [THIRD_PARTY.md](THIRD_PARTY.md).

## What it does

Packet Tracer runs an in-process JavaScript engine for *Script Modules*, but it has no socket an
outside program could connect to. This project bridges that gap with files: the MCP server writes a
JSON job into a directory, a small ES5 script running **inside** PT (`bridge/bridge.js`) performs the
op with the `ipc.*` API and writes the answer back. The agent talks MCP over stdio and never has to
know any of that.

```
  agent (opencode, Claude, …)
        │  MCP over stdio (JSON-RPC)
        ▼
  packet-tracer-mcp  ──write──▶  ~/pt/pt-mcp/jobs/<id>.json
        ▲                                            │  file watcher
        │                                            ▼
        └─────read─────  ~/pt/pt-mcp/results/<id>.json  ◀── bridge.js
                                                                  (inside PT)
```

Consequences worth knowing up front:

- **No networking at all**: no TCP port, no firewall rule, no auth token. The two sides share a
  directory (`PT_MCP_HOME`, default `~/pt/pt-mcp`).
- **The bridge acts on the active `.pkt` only.** Start PT, open the lab you want, then talk to it.
- **Latency is one poll** (50 ms) plus whatever the device takes; every call is a round trip you can
  inspect with `ls` and `cat`.
- **Offline-testable**: `tests/fake_bridge.py` speaks the same protocol, so the suite runs with no
  Packet Tracer installed.

## Architecture

Four moving parts, one shared directory:

| Part | Where | Role |
|---|---|---|
| MCP client | your agent | calls tools over stdio, never sees a file path |
| `pt_mcp.server` | this repo (`src/pt_mcp/`) | twenty-two MCP tools, atomic job write, 50 ms polling, cleanup |
| mailbox | `$PT_MCP_HOME/{jobs,results,saved}` | the entire transport: one JSON in, one JSON out |
| `bridge/bridge.js` | inside Packet Tracer | file watcher, op dispatch, `ipc.*` calls, result file |

The PT Script Engine is ES5/QtScript with no sockets and no wall-clock timers, so
`SystemFileManager` is the only reliable host bridge; that single constraint explains the whole
design. Jobs are processed serially against the **active** `.pkt`, a timed-out job is deleted
server-side so it cannot run behind your back, and `raw` is arbitrary JS in the engine; an
escape hatch, not a daily tool.

[docs/architecture.md](docs/architecture.md) has the flowcharts, the `configure` sequence,
execution semantics, the security/privileges model and a failure-mode table. The wire format is in
[docs/protocol.md](docs/protocol.md); the one-time PT-side setup and the **recovery procedure** for a
wedged console in [docs/setup.md](docs/setup.md); the CLI/mode rules, readiness and dry-run semantics
in [docs/cli-semantics.md](docs/cli-semantics.md); the Wayland screenshot recipe in
[docs/screenshot.md](docs/screenshot.md).

## Install

```sh
uv tool install "git+ssh://git@github.com/psampir/packet-tracer-mcp.git@main"
```

This installs the `packet-tracer-mcp` console script into `~/.local/bin`, so neither the
MCP configuration nor the PT setup needs a repository path. For development, use a
checkout instead: `uv run --directory /path/to/packet-tracer-mcp packet-tracer-mcp`.

Update later with `uv tool install --force "…@main"`.

## One-time Packet Tracer setup

Do this once per PT install; afterwards the module starts with PT automatically.

1. Packet Tracer 9.0.1 running, with a `.pkt` open (the bridge only sees the **active** file).
2. Download `packet-tracer-mcp.pts` from the
   [latest release](https://github.com/psampir/packet-tracer-mcp/releases/latest), then
   **Extensions → Scripting → Configure PT Script Modules… → Add…** and select it.
3. Select `packet-tracer-mcp` and click **Start**.
4. Quit once with **File → Exit**, so PT saves the registration.

The bundled `.pts` carries the name, ID, script and requested privileges; there is nothing
to paste. The manual build-from-source flow (paste `bridge.js`, set the name/ID and
privileges) lives in **[docs/setup.md](docs/setup.md)**, together with the rescan and
recovery procedures.

## Connect the MCP client

`examples/opencode-snippet.jsonc` holds the block to paste into the `mcp.servers` object of
`~/.config/opencode/opencode.jsonc` (add it, don't overwrite the file):

```jsonc
"packet-tracer": {
  "type": "local",
  "command": ["packet-tracer-mcp"],
  "disabled": false
}
```

`packet-tracer-mcp` is on `PATH` after the `uv tool install` above. Any stdio MCP client
works the same way:

```sh
packet-tracer-mcp
```

For a source checkout instead of the global tool:

```sh
uv run --directory /path/to/packet-tracer-mcp packet-tracer-mcp
```

Set `PT_MCP_HOME` if the bridge watches a different directory, and `PT_MCP_TIMEOUT` (default `120`
seconds) to change the per-call deadline.

## Tools

Twenty-two tools, all returning JSON strings. Failures never raise into the client: they come back as
`{"ok": false, "error": "…"}` with an actionable message.

The **host**, **topology** and **raw** tools are implemented server-side on top of the existing
`eval` bridge op: each generates an ES5 snippet, marker-tagged
`/* packet-tracer-mcp: <tool> */`, and unwraps the answer. That is why they need no new bridge op
and no re-paste of `bridge.js`: the Script Module you already have is enough.

| Tool | Arguments | What it does |
|---|---|---|
| `status` | (none) | Bridge health: PT version + device count of the active file |
| `list_devices` | (none) | Every device (name, type, typeName, model) and the link count |
| `add_device` | `name, type, model, x=100, y=100` | Add a device; `type` is a `DeviceType` name (`eRouter`) or int, `name=""` keeps PT's auto name. Returns `ready` (false while it boots) |
| `remove_device` | `name` | Remove a device |
| `link` | `a, a_port, b, b_port, cable="eCopperGigabitEthernet"` | Cable two ports; friendly cable names (`copper`, `crossover`, `fiber`, `serial`, `console`, …) are mapped to `CONNECT_TYPES` |
| `autolink` | `a, b` | Connect two devices, letting PT choose cable and ports |
| `configure` | `device, commands, mode="auto", keep_session=False, stop_on_error=True, dry_run=False, ready_timeout=45` | Send IOS CLI (string or list); blanks/`!` dropped, then the **bridge** adds `enable`/`configure terminal`/`end` depending on the prompt (never on a PC). `dry_run=True` returns the lines without sending |
| `show` | `device, command, ready_timeout=45` | Run one show/exec command and return the output |
| `power` | `device, on=True, ready=False, ready_timeout=45` | Power a device on or off; `ready=True` waits for the boot to finish |
| `skip_boot` | `device, wait=False, ready_timeout=45` | Press *Skip boot* so an IOS device reaches its console without the boot; `wait=True` also waits for the prompt |
| `list_links` | (none) | Every cable, one entry per physical link: `[{a, a_port, b, b_port, cable, up}]` |
| `list_ports` | `device` | A device's ports: `[{name, up, protocol_up, ip, mask, link}]`, `link` being the peer |
| `get_config` | `device, port=None` | Read addressing: `{"ports": [{name, ip, mask, dhcp, ipv6, up}]}` (one port or all) |
| `set_ip` | `device, port="FastEthernet0", ip=None, mask=None, gateway=None, dns=None, dhcp=False` | Static IPv4 (address first, mask second) or `dhcp=True` → `setDhcpClientFlag` + the DHCP client; returns the read-back `{ip, mask, dhcp}` |
| `set_ipv6` | `device, port, address, prefix=64, gateway=None` | Enable IPv6 on the port, add `address/prefix`, optional v6 gateway |
| `dns_record` | `device, name, ip, enable=True, zone=None, ns_name=None, soa=True, ns=True, mailbox=None, min_ttl=3600, refresh=3600, retry=3600, expiry=3600` | Add/remove a DNS A record: **both** `addIpAddress` and `addARecordToNameServerDb`, plus the zone's SOA/NS, so answers are not NXDOMAIN/non-authoritative |
| `dns_list` | `device, name=None` | Entries on a DNS server: `{"count", "entries"}` or `{"name", "ip"}` for one domain |
| `http_page` | `device, path="index.html", content=None, enable=True` | Start/stop the HTTP service, replace a page when `content` is given, return `{path, enabled, page}` |
| `pc_command` | `device, command, wait=10, ready_timeout=45` | Type into a PC/server shell and return the **whole** new output after `wait` seconds |
| `ping` | `src, dst, count=4, wait=10` | Ping from a host and parse it: `{sent, received, loss, replies, timed_out, unreachable, output}` (`count` is advisory; PT always sends four) |
| `save` | `path=""` | Save the active `.pkt` (default `~/pt/pt-mcp/saved/<ts>.pkt`) |
| `raw` | `js, max_chars=20000` | Escape hatch: evaluate JS in the PT engine. Returns a real JSON value, reports a thrown error with its JS line, and cuts long output with `"truncated": true` |

A typical session: `status` → `add_device` × n → `link` → `configure` → `show` →
`save`. Every `configure`/`show` waits for the device's prompt first (`ready_timeout`), and
`configure(..., dry_run=True)` previews the exact lines (including the wrappers) before anything
is typed. Mode rules and the per-line error format: [docs/cli-semantics.md](docs/cli-semantics.md).

End devices are configured through the host tools, not the CLI: `set_ip`/`set_ipv6`/
`dns_record`/`http_page` talk to the PT API directly (no `ipconfig` gymnastics), `pc_command`
types into the shell when you need the host's own output, and `ping` turns it into counts. Unknown
`type`/`cable` names are rejected *before* the call, with the valid list and a "Did you mean …?" hint.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Timed out … Packet Tracer is not responding` | PT not running, no `.pkt` open, or the Script Module stopped. Open a lab, then **Extensions → Scripting → Configure PT Script Modules… → `com.psampir.packet-tracer-mcp` → Start**. |
| `bridge was never set up: base dir … does not exist` | The module has never run, so `~/pt/pt-mcp` was never created. Start it once (or click **Run File**). |
| Jobs appear in `jobs/` but nothing answers | The file watcher isn't firing: click **Run File** in the Scripting interface to force a rescan. Also check the `PT_MCP_HOME` constant at the top of `bridge.js` matches the server's. |
| Privilege / access-denied errors in PT | Re-open the module's **Security Privileges** tab, enable **Application**, **File Operations** and **Get/Change Network Info**, then Stop → Start. |
| `device not found: X` | The name is wrong, or the device lives in a different (non-active) `.pkt`: the bridge only sees the active window. |
| Commands return empty output | The device is still booting. Every `configure`/`show` waits up to `ready_timeout` (45 s) for a prompt: raise it, power the device on with `power(..., ready=True)`, or use the device's "Skip boot" dialog. |
| `device 'R1' was not ready within 45s` | Still in `--- System Configuration Dialog ---` or booting | Increase `ready_timeout`, power-cycle with `power(on=True, ready=True)`, or answer the setup wizard manually once. |
| `stopped_on` in a `configure` result | The device rejected that line (`% Invalid input`, …) or never settled | Check `index`/`line`/`prompt`; rerun with `dry_run=True` to see the wrappers the bridge adds, or `mode="raw"` to send the block verbatim. |
| `link` fails | Port names are case-sensitive (`GigabitEthernet0/0`); try `autolink` to let PT pick compatible ports. |
| `unknown device type` / `unknown cable` | The name is not one PT knows. The error lists every valid value and suggests the closest: `add_device("R1", "Router", "2911")` works too (`e` optional, case-insensitive). |
| Console wedged: no prompt, calls do not land | Work through the ordered recovery procedure in [docs/setup.md §6](docs/setup.md#6-recovery-a-wedged-console): wait → power-cycle with `ready=True` → `skip_boot` → restart PT. A power-cycle **discards unsaved config**. |
| `nslookup` still says NXDOMAIN after a DNS entry | An entry needs both halves. `dns_record` writes `addIpAddress` **and** `addARecordToNameServerDb` (plus SOA/NS) in one call; re-add the record with it instead of using `raw`. |
| `raw` reports `PT API error: …` with a line number | The JS threw: often `getProcess("IP")` with a name shorter than the API expects. Read the message; the line number is where the engine failed. |
| Need a diagram for a report | PT 9.0.1 cannot export an image. Follow [docs/screenshot.md](docs/screenshot.md) (niri + grim under Wayland) or use the manual screenshot route. |
| PT was restarted | The module restarts with PT and recreates its directories; just retry the call. |

## Development

```sh
cd /path/to/packet-tracer-mcp
uv sync --extra dev
uv run pytest                                        # 200 Python tests, all offline
uv run pytest --cov=pt_mcp --cov-report=term-missing  # same run, 100% line coverage (gate: 85%)
node --test tests/bridge/*.test.js                   # 76 Node tests against the real bridge.js
uv run ruff check .                                  # lint
node --check bridge/bridge.js                        # ES5 bridge must stay parseable
```

The Python suite is driven by `tests/fake_bridge.py`, which answers on disk exactly
like `bridge/bridge.js` does; the Node suite is the other half of the contract:
`tests/bridge/harness.js` loads the **unmodified** `bridge/bridge.js` into a `node:vm`
context with a fake `ipc`, mock devices with an IOS prompt state machine, and an
in-memory `SystemFileManager`, so dispatch, the job/result file dance, the dedupe guard,
the watcher path and the CLI mode rules are all exercised without Packet Tracer. Only
Node built-ins are used (`node:test`, `node:assert`, `node:vm`), there are no npm
dependencies to install.

CI ([`ci.yml`](.github/workflows/ci.yml)) runs all three: `ruff check .`, the Node
suite and `pytest` under an **85% line-coverage gate** on `pt_mcp`
(`[tool.coverage.report] fail_under` plus `--cov-fail-under` in the workflow). Lower
the coverage only by adding tests, not by moving the threshold.

### Live smoke test (needs Packet Tracer)

`tools/live_smoke.py` builds a throw-away lab in the active `.pkt` and exercises
the paths only real PT can answer: add/link/list, prompt-aware configure
(including the IOS setup wizard and a rejected line), static IPv4/IPv6, DHCP,
DNS (A + SOA/NS), HTTP enable/keep/disable, ping success/failure, `raw`,
`skip_boot`, a power-cycle and `save`. It removes every device it created,
even when a check fails.

```sh
uv run python tools/live_smoke.py            # fast; routers use skip_boot
uv run python tools/live_smoke.py --slow     # + a full-boot router
uv run python tools/live_smoke.py --keep     # leave the lab for inspection
uv run python tools/live_smoke.py --verbose  # every device operation
```

It is **not** part of CI: it needs a running PT and the Script Module. A full run
on PT 9.0.1.0858 passes **16/16 checks**.

Layout:

| Path | Purpose |
|---|---|
| `src/pt_mcp/config.py` | paths (`PT_MCP_HOME`), timeout, `saved/` |
| `src/pt_mcp/protocol.py` | `call_pt()`, atomic job write, poll, cleanup; `PTBridgeError` |
| `src/pt_mcp/commands.py` | `normalize_commands()` for `configure` |
| `src/pt_mcp/registry.py` | the one MCP server instance and its instructions |
| `src/pt_mcp/readiness.py` | `wait_ready()`, poll until a device shows a usable prompt |
| `src/pt_mcp/tables.py` | device-type and cable names, validated before the call |
| `src/pt_mcp/eval.py` | the `eval` op, its snippet helpers, the `_tool` JSON wrapper |
| `src/pt_mcp/snippets.py` | the ES5 templates the host and topology tools ship |
| `src/pt_mcp/hosts.py` | host bodies: addresses, DNS, HTTP, console, ping |
| `src/pt_mcp/topology.py` | cable and port inventory bodies |
| `src/pt_mcp/raw.py` | the `raw` body: error-to-data wrapper, truncation |
| `src/pt_mcp/tools/` | the twenty-two tools, grouped observe/build/cli/hosts/raw |
| `src/pt_mcp/server.py` | entry point: imports the tools, re-exports them, `main()` |
| `bridge/bridge.js` | the ES5 script that runs **inside** Packet Tracer |
| `tests/fake_bridge.py` | protocol-compatible stand-in bridge for the Python suite |
| `tests/bridge/` | Node harness + tests that load the real `bridge.js` into `node:vm` |
| `tools/live_smoke.py` | live integration smoke test against a running Packet Tracer |
| `docs/` | [architecture](docs/architecture.md), [setup](docs/setup.md), [protocol](docs/protocol.md), [cli semantics](docs/cli-semantics.md), [screenshots](docs/screenshot.md) |

`bridge/bridge.js` is deliberately frozen ES5 (QtScript: `var`/`function` only, no `let`, arrows,
template literals or classes). It is copied into PT by hand, so keep changes minimal and re-paste it
into the module (Stop → Start) after editing.

## Branches

- **`dev`**: integration branch. All work lands here first; CI runs on every push.
- **`main`**: released code only. Merge `dev` into `main` when a version is complete, then tag.

```sh
# day to day
git switch dev
# ... work, commit ...
git push origin dev

# release time
git switch main
git merge --no-ff dev
git push origin main
git tag vX.Y.Z && git push origin vX.Y.Z
```

## Releasing

Publishing is triggered by a tag, never by hand, and only after `dev` has been merged into
`main`. The version in `pyproject.toml` is the single source of truth, and the tag must match it
exactly:

```sh
# bump "version" in pyproject.toml on dev, merge dev -> main, then
git tag v0.1.0
git push origin v0.1.0
```

The [`release`](.github/workflows/release.yml) workflow then:

1. **verifies**: strips the leading `v` and fails unless the tag equals `project.version` read
   from `pyproject.toml`, so a mistyped tag cannot produce a mislabelled artifact;
2. **tests**: `uv sync --frozen --extra dev`, `uv run ruff check .`, `uv run pytest -q` on the
   tagged commit;
3. **builds**: `uv build`, producing `dist/*.whl` and `dist/*.tar.gz`;
4. **publishes**: creates the GitHub Release for the tag with generated notes and attaches both
   artifacts (`gh release create`, `GH_TOKEN` = the workflow token).

There is no container image: the release *is* the two Python artifacts. If a run fails after the
tag was pushed, fix on `main`, then move the tag:

```sh
git push origin :refs/tags/v0.1.0     # delete the remote tag
git tag -f v0.1.0 && git push origin v0.1.0
```

CI ([`ci.yml`](.github/workflows/ci.yml)) runs lint + tests on every push to `main` and every pull
request, independent of releases.

## License

MIT; see [LICENSE](LICENSE) (`Copyright (c) 2026 Pawel Sampir`). Third-party notices are in
[THIRD_PARTY.md](THIRD_PARTY.md); "Cisco" and "Packet Tracer" are trademarks of Cisco Systems, Inc.

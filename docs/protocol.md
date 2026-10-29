# Wire protocol (`pt_mcp` ⇄ `bridge.js`)

There is **no socket** between the MCP server and Packet Tracer. Packet Tracer's
Script Module engine has no IPC listener, so the two sides talk through a
directory of JSON files that both sides poll. That is the whole protocol: one file
in, one file out, one request id per call.

```
 MCP client (agent)
        │  stdio JSON-RPC
        ▼
 pt_mcp.server ──write──▶ <PT_MCP_HOME>/jobs/<id>.json
        ▲                                  │  file watcher (or Run File)
        │                                  ▼
        └──read────── <PT_MCP_HOME>/results/<id>.json ◀── write ── bridge.js
                                                            (inside PT)
```

## Directories

| Path | Written by | Read by | Contents |
|---|---|---|---|
| `<base>/jobs/` | server | bridge | one request per call |
| `<base>/results/` | bridge | server | one response per call |
| `<base>/saved/` | server | — | default target for `save` |

`<base>` is `$PT_MCP_HOME`, default `/home/pawel/.local/share/pt-mcp`. The bridge
hard-codes the same default at the top of `bridge.js`; the two values must match,
otherwise every call times out.

## Request

`<base>/jobs/<id>.json`:

```json
{"id": "<id>", "op": "<op>", "args": { }}
```

- `id` — 12 hex characters (`uuid4().hex[:12]`), unique per call, also the
  file stem of both the job and its result.
- `op` — one of the ops below.
- `args` — op-specific object; `{}` when the op takes nothing.

### Atomicity

The server never writes `jobs/<id>.json` in place. It writes
`jobs/<id>.json.tmp` and then renames it with `os.replace()`, which is atomic on
POSIX within one filesystem. A watcher therefore only ever sees a **complete**
job file; `.tmp` files are ignored by the bridge. The same discipline applies on
the response side: the bridge writes the result in one call, and the server
retries (every 50 ms) if it catches a partially written or unparsable file instead
of failing outright.

## Response

`<base>/results/<id>.json` — exactly one of:

```json
{"id": "<id>", "ok": true,  "result": { }}
{"id": "<id>", "ok": false, "error": "message"}
```

`result` is op-specific (object, array, number or string). `error` is a
human-readable string; the server turns it into a `PTBridgeError`, and the MCP
tool renders that as `{"ok": false, "error": "Packet Tracer bridge error for op
'…': …"}` so the agent gets text it can act on.

## Ops

| Op | `args` | `result` | MCP tool |
|---|---|---|---|
| `ping` | — | `{"version": "9.0.1…", "deviceCount": N}` | `status` |
| `list_devices` | — | `{"devices": [{name, type, typeName, model}], "links": N}` | `list_devices` |
| `add_device` | `{name, type, model, x, y}` | `{"name": "<actual name>", "ready": bool}` | `add_device` |
| `remove_device` | `{name}` | `{"removed": true\|false}` | `remove_device` |
| `link` | `{a, aPort, b, bPort, cable}` | `{"linked": true}` | `link` |
| `autolink` | `{a, b}` | `{"ok": true}` | `autolink` |
| `get_state` | `{device}` | `{"name", "prompt", "mode", "booting", "hasIos", "type", "model"}` | — (used by `wait_ready`) |
| `plan` | `{device, lines, mode, keep_session}` | `{"lines": [string], "mode": "auto", "effective_mode": "raw"\|"exec"\|"config", "prompt", "booting"}` | `configure(dry_run=True)` |
| `configure` | `{device, lines, mode, keep_session, stop_on_error, timeout_ms}` | `{"prompt", "lines": [{line, prompt, output, ok}], "output", "stopped_on", "ok", "mode", "effective_mode"}` | `configure` |
| `exec` | `{device, command, timeout_ms}` | same shape as `configure`, `effective_mode: "exec"` | `show` |
| `show` | `{device, command}` | alias of `exec`, kept for older clients | — |
| `power` | `{device, on: bool}` | `{"ok": true}` | `power` |
| `skip_boot` | `{device}` | `{"ok": true}` | `skip_boot`, `add_device(skip_boot=True)` |
| `save` | `{path}` | `{"saved": true, "path": "…"}` | `save` |
| `eval` | `{js}` | `{"value": "<stringified>"}` | `raw` and every host/topology tool (server-side, see below) |

`mode` in `plan`/`configure` is `auto` (default), `raw`, `exec` or `config`; the
bridge resolves it against the device's prompt and reports the outcome as
`effective_mode`. `keep_session` suppresses the trailing `end`,
`stop_on_error` (default true) stops at the first rejected line, and
`timeout_ms` (default 3000) bounds each line. `lines[].output` is that line's
delta only; `stopped_on` is `{index, line, prompt, reason, output}` with a
**1-based** index. Full rules: [cli-semantics.md](cli-semantics.md).

`type` in `add_device` accepts a `DeviceType` name (`"eRouter"`, `"eSwitch"`,
`"ePc"`, …) or the integer value (0 = router) and is resolved inside the bridge.
The **server** validates it first, too: an unknown name never reaches PT, and the
error lists every valid value plus a closest match (PTM-09). `cable` in `link` is
the same story for `"eCopperEthernet"`, `"eCopperGigabitEthernet"`,
`"eFiberGigabitEthernet"`, `"straight"`, `"crossover"`, `"fiber"`, `"serial"`,
`"console"`, `"wireless"`, `"coax"`, `"auto"` or a raw `CONNECT_TYPES` integer
(PTM-10).

## Server-side helpers over `eval`

The host and topology tools need the PT API, not the console, and adding a bridge
op for each of them would mean re-pasting `bridge.js` into Packet Tracer every
time. Instead they are generated **server-side** and shipped through the `eval`
op that has always existed:

| Tool | PT API it touches |
|---|---|
| `get_config` | `Port.getIpAddress/getSubnetMask/isDhcpClientOn/getIpv6Addresses/isPortUp` |
| `set_ip` | `Port.setIpSubnetMask(ip, mask)` (**ip first**), `setDefaultGateway`, `setDnsServerIp`, `setDhcpClientFlag`, `Device.getProcess("DhcpClient").dhcpRun(port)` |
| `set_ipv6` | `Port.setIpv6Enabled(true)`, `addIpv6Address(addr, prefix, 0, false)`, `setv6DefaultGateway` |
| `dns_record` / `dns_list` | `Device.getProcess("DnsServer")`: `addIpAddress` **and** `addARecordToNameServerDb`, `addSOAToNameServerDb`, `addNSRecordToNameServerDb`, `removeIpAddress`, `removeARecordFromNameServerDb`, `getEntryCount/getEntryAt/getIpAddOfDomain` |
| `http_page` | `Device.getProcess("HttpServer")`: `setEnable`, `isEnabled`, `setPageContents`, `getPage` |
| `pc_command` / `ping` | `Device.getCommandLine().getOutput()` plus the `exec` op for the keystrokes |
| `list_links` | `Port.getLink()` → `Cable.getPort1/getPort2/getConnectionType`, `Port.isPortUp` |
| `list_ports` | `Device.getPortCount/getPortAt`, `Port.getName/isPortUp/isProtocolUp/getLink` |

Each snippet starts with a `/* packet-tracer-mcp: <tool> */` comment and returns JSON, so the
`value` of the `eval` result carries one more JSON layer; the server parses it
twice while it is still a string. A thrown error comes back as the op's `error`,
which becomes `PT API error: <message> (op=<tool>, device=<device>)`.

Consequences for the wire format:

- **no new ops** — the installed Script Module keeps working, and every one of
  these tools works against an older `bridge.js`;
- the PT-side cost is one `eval` op per call (two for `pc_command`);
- `raw` is still the escape hatch, but it no longer has to be the *only* way to
  reach the API (PTM-03).

Because `eval` is arbitrary JS, the server does not restrict what a snippet may
do — only the installed script's Security Privileges and PT's own API do. Read
[architecture.md](architecture.md#security-model) before widening them.

## Lifecycle and cleanup

A successful call, in order:

1. server writes `jobs/<id>.json.tmp`, renames to `jobs/<id>.json`;
2. bridge reads it, performs the op;
3. bridge writes `results/<id>.json`, then deletes `jobs/<id>.json`
   (`bridge.js` is idempotent: a re-run never double-processes a job);
4. server sees the result, **deletes it**, returns `result`.

A timed-out call (default 120 s, `PT_MCP_TIMEOUT`):

- the server **deletes its own `jobs/<id>.json`** before raising, so a bridge that
  comes back later cannot execute a stale command — a half-applied config from a
  job nobody is waiting for is worse than no config;
- raises `PTBridgeError` with a checklist of PT-side causes (PT not running, no
  active `.pkt`, module not started, watcher silent, privileges missing).

Other rules:

- Stale results are harmless. If `results/<id>.json` exists but carries a
  different `id`, the server leaves it alone and keeps polling for its own.
- Directories are created on the first call (`jobs/`, `results/`), and `saved/`
  on the first `save` without an explicit path. If `<base>` itself is missing,
  nothing is answered at all, so the server reports "the packet-tracer-mcp bridge was never
  set up" instead of creating a directory PT is not watching.
- The bridge only ever touches the **active** `.pkt`. A call cannot reach a lab
  that is open in another window.

## Why files instead of a socket

- PT's Script Module has no server socket, and importing a socket library inside
  the engine is not an option (ES5/QtScript, sandboxed).
- A directory is observable by both sides with zero dependencies, survives a PT
  restart without reconfiguring anything, and is trivial to debug: `ls jobs`,
  `cat` a request, `cat` a response.
- Cost: latency is one polling interval (50 ms) rather than microseconds, and
  every call is serialized through the filesystem. Both are invisible next to the
  seconds a device takes to apply a CLI line.

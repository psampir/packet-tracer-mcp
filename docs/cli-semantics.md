# CLI semantics: modes, prompts, waiting and dry runs

`pt_configure` is the only tool that has to get a *modal* CLI right. This page
defines exactly what the two sides do, so a surprising `% Invalid input` can be
traced to a rule instead of guessed at.

The one-sentence version: **the server splits lines, the bridge decides what to
type, and the server waits for the device to be ready before asking.**

| Layer | Responsibility |
|---|---|
| `src/pt_mcp/commands.py` | split a string/list into lines, drop blanks and `!` comments, strip whitespace |
| `src/pt_mcp/server.py` | wait for a usable prompt (`wait_ready`), then send; `dry_run` short-circuits before waiting |
| `bridge/bridge.js` | read the prompt, add `enable` / `configure terminal` / `end` where they belong, send line by line, report what each line did |

There is deliberately **no mode policy in Python**: only the bridge can see the
prompt after every line, and only the bridge knows whether a device is a PC
(`C:\>`) or a router (`R1#`).

## 1. Prompt detection

`get_state` classifies the console prompt (`R1#`, `Switch>`, `S1(config-if)#`,
`C:\>`, …) into one of five modes:

| Prompt (examples) | `mode` | Meaning |
|---|---|---|
| `C:\>` | `shell` | a host shell (PC, server, laptop) — not IOS at all |
| `Switch>` | `user` | IOS user exec |
| `Switch#`, `R1#` | `enable` | IOS privileged exec |
| `S1(config)#`, `S1(config-if)#`, `R1(dhcp-config)#` | `config` | any prompt containing `(config…` and ending in `#` |
| empty, `--- System Configuration Dialog ---`, anything else | `unknown` | booting, wizard, or an unrecognised prompt |

`hasIos` is an **allowlist of DeviceType ids**, not a guess from the prompt:

| Type | `hasIos` |
|---|---|
| `eRouter` (0), `eSwitch` (1), `eMultiLayerSwitch` (16), `eASA` (26), `eWirelessLanController` (39) | `true` |
| everything else — `ePc` (8), `eServer` (9), `ePrinter` (10), `eLaptop` (17), `eAccessPoint` (7), phones, IoT, … | `false` |

`booting` comes from `device.isBooting()` when the build exposes it, otherwise
from the prompt: empty, or containing `System Configuration Dialog` / `Press
RETURN`, means still booting.

## 2. Modes

`pt_configure(..., mode=...)` and the bridge's `plan`/`configure` ops accept:

| `mode` | What is sent |
|---|---|
| `auto` *(default)* | inspect the prompt and pick one of the behaviours below |
| `raw` | exactly the lines given — no `enable`, no `configure terminal`, no `end` |
| `exec` | exec commands only: `enable` if the prompt is `user`, never enters config, never appends `end` |
| `config` | `enable` if `user`, then `configure terminal` unless already in config, then the body, then `end` unless `keep_session` |

`auto` resolves per prompt:

| Prompt mode | What `auto` does |
|---|---|
| `shell`, `unknown` | behaves like `raw` |
| `user` | prepends `enable`, then re-decides as if the prompt were `enable` |
| `enable` | if the first meaningful line starts with an exec verb (`show`, `ping`, `traceroute`, `telnet`, `ssh`, `clear`, `copy`, `write`, `reload`, `dir`, `more`, `debug`, `undebug`, `clock`, `exit`, `enable`, `disable`, `configure`, `terminal`) it behaves like `exec`; otherwise it prepends `configure terminal` and appends `end` (unless `keep_session`) |
| `config` | no prefix; appends `end` unless `keep_session` |

And a device with `hasIos: false` is **always** `raw`, whatever mode was asked
for: a PC gets zero insertions, so `pt_configure("PC-Users", ["ping 1.1.1.1"])`
types exactly one line.

`keep_session=true` never appends `end`: the device stays in config mode, so a
series of calls builds one session. Remember to finish the last one with
`mode="config"` and an explicit `end` (or `raw` + `end`). A block that already
finishes with `end` does not get a second one.

`exec`/`pt_show` never leave config mode either — while the device sits at
`S1(config)#`, a `show` line is typed there and the device will reject it. Finish
the config session (`end`) before reading `show` output.

Results echo both values so the decision is visible:

```json
{"prompt": "S1#", "mode": "auto", "effective_mode": "config", "lines": [...]}
```

`mode` is what the caller asked for; `effective_mode` (`raw` | `exec` | `config`)
is what the bridge actually did.

## 3. Per-line send and what counts as an error

Before each line the bridge records the prompt and the console output length,
sends **exactly one** line, and then polls every ~15 ms up to `timeout_ms`
(default 3000):

- `flush(10)` is called if the command line has `flush` (`TerminalLine` does,
  `ConsoleLine` does not — errors are ignored);
- it stops as soon as the **prompt changes**, or when the output buffer has not
  grown for **120 ms**;
- the line's `output` is the delta of that line only (no scrollback).

A line is `ok: false` when the delta matches `% Invalid input`, `% Incomplete`,
`% Ambiguous`, `% Unknown`, `% Please answer` or `Translating "` — or when it
never settled within `timeout_ms`.

A failing line is **reported, never skipped silently**. The result carries:

```json
{
  "ok": false,
  "prompt": "S1(config)#",
  "mode": "auto",
  "effective_mode": "config",
  "output": "...",
  "lines": [{"line": "vlan 10", "prompt": "S1(config)#", "output": "vlan 10\n", "ok": true}],
  "stopped_on": {
    "index": 3,
    "line": "bad-line",
    "prompt": "S1(config)#",
    "reason": "rejected",
    "output": "bad-line\n% Invalid input detected at '^' marker.\n"
  },
  "failed": [3],
  "error": "line 3 'bad-line' was rejected at prompt 'S1(config)#'"
}
```

- `index` is **1-based and counts the injected wrapper lines**, so it points at
  the exact line in `lines` above it.
- `reason` is `rejected` (the device answered with a `%` error) or `timeout`
  (no prompt change and no output growth within `timeout_ms`).
- `stop_on_error=true` (default) stops at the first failure; `failed` then has
  one entry. With `stop_on_error=false` the whole block still runs, `failed`
  lists every bad line, and `stopped_on` describes the first one.

## 4. Readiness (PTM-01)

A freshly added router sits in `--- System Configuration Dialog ---` for tens of
seconds; a switch under boot answers `% Invalid input` to everything. Both turn
a config push into garbage, so the **server** waits before it types:

- `pt_add_device` returns `{"name": ..., "ready": true|false}` immediately (it
  never blocks);
- `wait_ready(device, timeout)` polls `get_state` every **250 ms** (real
  `time.sleep`, outside the bridge) until `booting` is false **and** the prompt
  mode is `user`, `enable`, `config` or `shell`;
- if the prompt is the IOS **initial-configuration dialog** (`… [yes/no]:`), it
  is answered **once** with `no` and polling continues; a dialog that stays put
  is reported as such (click *Skip boot* or power-cycle);
- `pt_configure` and `pt_show` call it first with `ready_timeout` (default
  45 s); `pt_power(..., ready=True)` does the same after powering a device on;
- on timeout the tool answers `{"ok": false, "error": "device 'R1' was not ready
  within 45s (prompt='', mode='unknown', booting=True). …"}` — with the device,
  the last prompt and the budget, so the fix (longer `ready_timeout`, a
  power-cycle, or skipping the boot wizard) is obvious.

The bridge itself never busy-waits for a boot: the per-line wait is milliseconds,
readiness is seconds, and the server is the side with real timers.

## 5. `dry_run`

`pt_configure(..., dry_run=True)` calls the bridge's `plan` op and returns the
sequence it *would* send, without touching the console — and without waiting for
readiness, so it works on a device that is still booting:

```json
{"dry_run": true, "lines": ["ping 8.8.8.8"], "mode": "auto",
 "effective_mode": "raw", "prompt": "C:\\>", "booting": false}
```

`prompt` and `booting` are part of the answer on purpose: while `booting` is
true the prompt is not known yet, so `auto` cannot wrap anything and the plan is
**provisional** — re-run it once the device is ready. For a ready device the
plan is exactly what will be typed.

This is the cheapest way to catch a mode bug (PTM-02) before it destroys a
session: preview, then send.

## 6. What a PC sees

For a `hasIos: false` device, all of the above collapses to `raw`:

```json
pt_configure("PC-Users", ["ping 192.168.20.10"], dry_run=True)
{"dry_run": true, "lines": ["ping 192.168.20.10"], "mode": "auto", "effective_mode": "raw"}
```

No `enable`, no `configure terminal`, no `end` — the three lines that used to
produce `Invalid Command.` ahead of the actual ping (and, on a switch in exec
mode, a DNS lookup that locked the console).

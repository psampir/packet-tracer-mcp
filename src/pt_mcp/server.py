"""MCP server exposing Cisco Packet Tracer to an AI agent over stdio.

Twenty-two tools, in five groups:

* **observe** — :func:`pt_status`, :func:`pt_list_devices`, :func:`pt_show`
* **build** — :func:`pt_add_device`, :func:`pt_remove_device`, :func:`pt_link`,
  :func:`pt_autolink`, :func:`pt_power`, :func:`pt_skip_boot`
* **topology** — :func:`pt_list_links`, :func:`pt_list_ports`
* **hosts** — :func:`pt_get_config`, :func:`pt_set_ip`, :func:`pt_set_ipv6`,
  :func:`pt_dns_record`, :func:`pt_dns_list`, :func:`pt_http_page`,
  :func:`pt_pc_command`, :func:`pt_ping`
* **change / escape** — :func:`pt_configure`, :func:`pt_save`, :func:`pt_raw`

They all funnel into :func:`pt_mcp.protocol.call_pt`, i.e. one JSON file in
``<PT_MCP_HOME>/jobs`` and one out of ``<PT_MCP_HOME>/results``; the ops are
implemented by ``bridge/bridge.js`` running inside Packet Tracer, which always
acts on the **active** ``.pkt`` file.

Console semantics live in the bridge, not here: it reads the prompt before and
after every line, decides whether ``enable`` / ``configure terminal`` / ``end``
belong in the sequence, and waits for each line to be absorbed. This module adds
the one thing the bridge cannot do — *real waiting*: :func:`wait_ready` polls
``get_state`` with :func:`time.sleep` until a freshly added or freshly powered
device has a usable prompt (PTM-01). See ``docs/cli-semantics.md``.

The **host** and **topology** tools need the Packet Tracer API rather than the
console, so they are implemented *server-side on top of the existing ``eval`` op*
(PTM-03/04/12): every one of them generates an ES5 snippet with a
``/* pt-mcp: <tool> */`` marker, ships it through ``eval``, and unwraps the
double-encoded answer in :func:`_eval`. The installed Script Module therefore
keeps working unchanged — no re-paste, no new bridge op.

Every tool returns a **JSON string** — a result, or ``{"ok": false, "error"}``
so an LLM client always gets something it can act on instead of a traceback.

SDK note: ``mcp`` 2.x renamed ``FastMCP`` to ``MCPServer``
(``from mcp.server.mcpserver import MCPServer``); the import below falls back to
the v1 ``FastMCP`` name so either major works. Transport: stdio only —
``main()`` starts the server with ``server.run("stdio")`` and prints nothing, so
the stream stays clean for the JSON-RPC handshake.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import re
import string
import time
from datetime import datetime
from typing import Any

try:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _MCPServerType
except ImportError:  # pragma: no cover - only hit with mcp < 2
    from mcp.server.fastmcp import FastMCP as _MCPServerType

from . import __version__, config
from .commands import normalize_commands
from .protocol import PTBridgeError, _err, _ok, call_pt, not_set_up, run_op

_INSTRUCTIONS = f"""\
Packet Tracer bridge (pt-mcp {__version__}). Packet Tracer must be running with a
.pkt file open and the com.psampir.ptmcp Script Module started; every tool acts on
the ACTIVE file.

Typical flow:
  1. pt_status()            -> PT version + device count (also proves the bridge works)
  2. pt_list_devices()      -> names, types, models, link count
  3. pt_add_device(...)     -> name, type (eDeviceType name or int), model, x, y
  4. pt_link(a, a_port, b, b_port) / pt_autolink(a, b)
  5. pt_configure(...)      -> IOS CLI; mode=auto|raw|exec|config, dry_run=True to preview
  6. pt_show(...)           -> one show/exec command
  7. pt_power(...) / pt_save(...)

Hosts and topology (no IOS needed, all server-side over the bridge's eval op):
  pt_get_config, pt_set_ip, pt_set_ipv6, pt_dns_record, pt_dns_list,
  pt_http_page, pt_pc_command, pt_ping, pt_list_links, pt_list_ports,
  pt_skip_boot.

Commands are wrapped for the mode the device is actually in (enable / configure
terminal / end), and a PC's shell gets no wrappers at all. Every call waits for
the device's prompt first (ready_timeout, default 45 s).

All results are JSON strings; failures come back as {{"ok": false, "error": ...}}.
If a call times out, Packet Tracer is not answering: check that PT is running, a
.pkt is open, the Script Module is Started (Extensions -> Scripting ->
Configure PT Script Modules...) and, if the file watcher is silent, click
'Run File' to force a rescan. pt_raw(js) evaluates arbitrary JS in the PT engine
and is an escape hatch, not a normal step.
"""

server = _MCPServerType("packet-tracer-mcp", instructions=_INSTRUCTIONS)

#: How often :func:`wait_ready` asks the bridge for the device state, in seconds.
READY_POLL_INTERVAL = 0.25

#: Prompt modes that mean "the console is usable" (see docs/cli-semantics.md).
_READY_MODES = frozenset({"user", "enable", "config", "shell"})

#: Default readiness budget, in seconds.
READY_TIMEOUT = 45.0

#: A fresh IOS device may park at the initial-configuration dialog instead of a
#: prompt. ``no`` drops it to the normal console; the regex identifies the wait.
_SETUP_DIALOG_RE = re.compile(
    r"(initial configuration dialog|would you like to enter|\[yes/no\])",
    re.IGNORECASE,
)


def wait_ready(device: str, timeout: float = READY_TIMEOUT) -> dict[str, Any]:
    """Block until ``device`` shows a usable prompt, or raise :class:`PTBridgeError`.

    Polls ``get_state`` every :data:`READY_POLL_INTERVAL` seconds (real
    ``time.sleep``; the bridge must not busy-wait for a boot) until the device
    reports ``booting: false`` and a prompt mode of ``user``, ``enable``,
    ``config`` or ``shell``. A freshly added router sits in
    ``--- System Configuration Dialog ---`` for tens of seconds, and commands
    sent before that are eaten as wizard answers (PTM-01).
    """
    deadline = time.monotonic() + max(timeout, 0.0)
    state: dict[str, Any] = {}
    answered_setup = False
    pressed_return = False

    while True:
        state = call_pt("get_state", {"device": device})
        prompt = str(state.get("prompt") or "")

        if _SETUP_DIALOG_RE.search(prompt):
            # A freshly added IOS device can sit at the initial-configuration
            # dialog; answer "no" once to reach the normal prompt. If it does
            # not take, the deadline below reports the dialog specifically.
            if not answered_setup:
                # Keep polling even if the answer fails: the deadline is
                # authoritative and its error names the dialog.
                with contextlib.suppress(PTBridgeError):
                    call_pt(
                        "exec",
                        {"device": device, "command": "no", "timeout_ms": 5000},
                    )
                answered_setup = True
                if time.monotonic() < deadline:
                    time.sleep(READY_POLL_INTERVAL)
                continue
            if time.monotonic() >= deadline:
                raise PTBridgeError(
                    f"device '{device}' is stuck at the IOS initial-configuration "
                    f"dialog after answering 'no' (prompt={state.get('prompt')!r}).\n"
                    "Click 'Skip boot' in the device's config dialog, or "
                    "power-cycle it with pt_power(on=True, ready=True)."
                )
            time.sleep(READY_POLL_INTERVAL)
            continue

        # After "no" IOS prints "Press RETURN to get started!" and shows no
        # prompt; an empty line is the answer. Send it exactly once.
        if answered_setup and not pressed_return and not prompt:
            with contextlib.suppress(PTBridgeError):
                call_pt(
                    "configure",
                    {
                        "device": device,
                        "lines": [""],
                        "mode": "raw",
                        "stop_on_error": False,
                    },
                )
            pressed_return = True
            if time.monotonic() < deadline:
                time.sleep(READY_POLL_INTERVAL)
            continue

        if not state.get("booting") and state.get("mode") in _READY_MODES:
            return state
        if time.monotonic() >= deadline:
            raise PTBridgeError(
                f"device '{device}' was not ready within {timeout:g}s "
                f"(prompt={state.get('prompt')!r}, mode={state.get('mode')!r}, "
                f"booting={state.get('booting')}).\n"
                "The device is probably still booting: retry with a longer "
                "ready_timeout, power-cycle it with pt_power(on=True, ready=True), "
                "or click 'Skip boot' in its config dialog."
            )
        time.sleep(READY_POLL_INTERVAL)


def _wait_or_error(device: str, timeout: float) -> str | None:
    """Run :func:`wait_ready`, returning the JSON error string on failure."""
    try:
        wait_ready(device, timeout)
    except PTBridgeError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001 - surface anything to the client
        return _err(f"Unexpected error waiting for device '{device}': {exc}")
    return None


def _tool(name: str, fn: Any, *args: Any) -> str:
    """Run a tool body, rendering any failure as the usual ``ok: false`` JSON.

    Every new tool's body returns a plain Python value; this wrapper is the one
    place that turns a :class:`PTBridgeError` (or a surprise) into the JSON
    string an LLM client can act on.
    """
    try:
        return _ok(fn(*args))
    except PTBridgeError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001 - surface anything to the client
        return _err(f"Unexpected error in {name}: {exc}")


# --------------------------------------------------------------------------
# Packet Tracer API over the bridge's ``eval`` op
#
# Everything below is generated ES5 that runs inside the PT Script Engine. It
# has no access to Python: the bridge's ``eval`` op evaluates it and returns
# ``{"value": safeStringify(result)}``, i.e. a JSON string holding JSON. The
# marker comment on each snippet keeps ``pt_raw`` and the fake bridge honest
# about which tool produced a script.
# --------------------------------------------------------------------------
#: ``DeviceType`` values, identical to ``DEVICE_TYPES`` in ``bridge/bridge.js``
#: (PT 9.0.1). Used to validate ``pt_add_device`` *before* the call (PTM-09).
DEVICE_TYPES: dict[str, int] = {
    "eRouter": 0,
    "eSwitch": 1,
    "eCloud": 2,
    "eBridge": 3,
    "eHub": 4,
    "eRepeater": 5,
    "eCoAxialSplitter": 6,
    "eAccessPoint": 7,
    "ePc": 8,
    "eServer": 9,
    "ePrinter": 10,
    "eWirelessRouter": 11,
    "eIpPhone": 12,
    "eDslModem": 13,
    "eCableModem": 14,
    "eRemoteNetwork": 15,
    "eMultiLayerSwitch": 16,
    "eLaptop": 17,
    "eTabletPC": 18,
    "ePda": 19,
    "eWirelessEndDevice": 20,
    "eWiredEndDevice": 21,
    "eTV": 22,
    "eHomeVoip": 23,
    "eAnalogPhone": 24,
    "eMultiUser": 25,
    "eASA": 26,
    "eIoE": 27,
    "eHomeGateway": 28,
    "eCellTower": 29,
    "eCentralOfficeServer": 30,
    "eCiscoAccessPoint": 31,
    "eEmbeddedCiscoAccessPoint": 32,
    "eSniffer": 33,
    "eMCU": 34,
    "eSBC": 35,
    "eThing": 36,
    "eMCUComponent": 37,
    "eEmbeddedServer": 38,
    "eWirelessLanController": 39,
    "eCluster": 40,
}

#: Cable names accepted by ``pt_link``, mapped to the bridge's ``CONNECT_TYPES``
#: integers (PTM-10). The ``e…`` names are the PortType spellings people (and
#: the PT docs) use; ``straight``/``crossover``/… are the friendly ones.
CABLES: dict[str, int] = {
    "eCopperEthernet": 8100,
    "eCopperFastEthernet": 8100,
    "eCopperGigabitEthernet": 8100,
    "eFiberFastEthernet": 8103,
    "eFiberGigabitEthernet": 8103,
    "eSerial": 8106,
    "eCopperCoaxial": 8110,
    "straight": 8100,
    "crossover": 8101,
    "fiber": 8103,
    "serial": 8106,
    "console": 8108,
    "wireless": 8109,
    "coax": 8110,
    "auto": 8107,
}


def _aliases(names: dict[str, Any]) -> dict[str, Any]:
    """Lower-case lookup for ``names``, also without the leading ``e``.

    ``"eRouter"`` can be written ``EROUTER``, ``erouter``, ``Router`` or
    ``router``; everything lands on the canonical key.
    """
    out: dict[str, Any] = {}
    for name, value in names.items():
        out[name.lower()] = value
        if name[:1] == "e" and name[1:]:
            out.setdefault(name[1:].lower(), value)
    return out


_DEVICE_ALIASES = _aliases(DEVICE_TYPES)
_CABLE_ALIASES = _aliases(CABLES)


def _unknown(kind: str, value: object, names: dict[str, Any], aliases: dict[str, Any]) -> str:
    """A "what you wrote, what is allowed, what you meant" error message."""
    text = str(value)
    canonical_names = list(names)
    message = f"unknown {kind} '{text}'; valid: {', '.join(canonical_names)}"
    close = difflib.get_close_matches(text.lower(), list(aliases), n=1, cutoff=0.6)
    if close:
        target = aliases[close[0]]
        suggestion = next(
            (name for name in canonical_names if names[name] == target), None
        )
        if suggestion:
            message += f"\nDid you mean {suggestion}?"
    return message


def resolve_device_type(value: int | str) -> int:
    """Validate a ``DeviceType``: name (with or without ``e``), int or digits."""
    if isinstance(value, bool):  # bool is an int subclass; not a device type
        raise ValueError(_unknown("device type", value, DEVICE_TYPES, _DEVICE_ALIASES))
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    if text.lower() in _DEVICE_ALIASES:
        return _DEVICE_ALIASES[text.lower()]
    raise ValueError(_unknown("device type", value, DEVICE_TYPES, _DEVICE_ALIASES))


def resolve_cable(value: int | str) -> int:
    """Validate a cable name (friendly or ``e…``) or a raw ``CONNECT_TYPES`` int."""
    if isinstance(value, bool):
        raise ValueError(_unknown("cable", value, CABLES, _CABLE_ALIASES))
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    if text.lower() in _CABLE_ALIASES:
        return _CABLE_ALIASES[text.lower()]
    raise ValueError(_unknown("cable", value, CABLES, _CABLE_ALIASES))


def _lit(value: Any) -> str:
    """Render ``value`` as a JavaScript literal (JSON is a subset of ES5)."""
    return json.dumps(value, ensure_ascii=False)


def _marker(name: str) -> str:
    """The comment that identifies a generated snippet in ``pt_raw``/logs."""
    return f"/* pt-mcp: {name} */"


def _tmpl(template: string.Template, **values: str) -> str:
    """Substitute already-encoded (:func:`_lit` / code) values into ``template``."""
    return template.substitute(values)


def _api_error(message: str, device: str | None, op: str) -> str:
    """Prefix a failure with ``PT API error:`` and add its context (PTM-11)."""
    context = f" (op={op}" + (f", device={device}" if device else "") + ")"
    if message.startswith("PT API error:"):
        return message + context
    return f"PT API error: {message}{context}"


def _eval(js: str, device: str | None = None, op: str = "eval") -> Any:
    """Evaluate ``js`` inside PT and return the unwrapped ``value``.

    The bridge answers ``{"value": "<json>"}`` (``safeStringify`` of whatever the
    script returned), and the transport adds its own JSON layer, so the value is
    parsed twice while it is still a string. Every failure becomes a
    :class:`PTBridgeError` that names the device and the tool (PTM-11).
    """
    try:
        result = call_pt("eval", {"js": js})
    except PTBridgeError as exc:
        raise PTBridgeError(_api_error(str(exc), device, op)) from exc

    if not isinstance(result, dict) or "value" not in result:
        raise PTBridgeError(
            _api_error(f"unexpected eval result (no 'value'): {result!r}", device, op)
        )

    value: Any = result["value"]
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except ValueError:
            break
    return value


_DEVICE_LOOKUP = string.Template(
    """
    var net = ipc.network();
    var dev = null;
    for (var scan = 0; scan < net.getDeviceCount(); scan += 1) {
        var candidate = net.getDeviceAt(scan);
        if (candidate && String(candidate.getName()) === $want) {
            dev = candidate;
            break;
        }
    }
    if (dev === null) { throw new Error("device not found: " + $want); }
"""
)


def _device_js(device: str) -> str:
    """ES5 that finds ``device`` in the active workspace or throws."""
    return _DEVICE_LOOKUP.substitute(want=_lit(device))


_GET_CONFIG_JS = string.Template(
    """$marker(function () {
    $lookup
    function readPort(port, index) {
        var name = "Port" + index;
        var ip = "";
        var mask = "";
        var dhcp = false;
        var up = false;
        var v6 = [];
        try { name = String(port.getName()); } catch (e1) { name = "Port" + index; }
        try { ip = String(port.getIpAddress()); } catch (e2) { ip = ""; }
        try { mask = String(port.getSubnetMask()); } catch (e3) { mask = ""; }
        try { dhcp = !!port.isDhcpClientOn(); } catch (e4) { dhcp = false; }
        try { up = !!port.isPortUp(); } catch (e5) { up = false; }
        try {
            var rawV6 = port.getIpv6Addresses();
            if (rawV6) {
                if (typeof rawV6.length === "number") {
                    for (var j = 0; j < rawV6.length; j += 1) { v6.push(String(rawV6[j])); }
                } else {
                    v6.push(String(rawV6));
                }
            }
        } catch (e6) { v6 = []; }
        return {name: name, ip: ip, mask: mask, dhcp: dhcp, ipv6: v6, up: up};
    }
    var ports = [];
$collect
    return ports;
}())"""
)

_SET_IP_JS = string.Template(
    """$marker(function () {
    $lookup
    var port = null;
    try { port = dev.getPort($port); } catch (e1) { port = null; }
    if (!port) { throw new Error("port not found: " + $port); }
$action
    var result = {port: $port, ip: "", mask: "", dhcp: false};
    try { result.ip = String(port.getIpAddress()); } catch (e2) { result.ip = ""; }
    try { result.mask = String(port.getSubnetMask()); } catch (e3) { result.mask = ""; }
    try { result.dhcp = !!port.isDhcpClientOn(); } catch (e4) { result.dhcp = false; }
    return result;
}())"""
)

_SET_IPV6_JS = string.Template(
    """$marker(function () {
    $lookup
    var port = null;
    try { port = dev.getPort($port); } catch (e1) { port = null; }
    if (!port) { throw new Error("port not found: " + $port); }
    port.setIpv6Enabled(true);
    port.addIpv6Address($address, $prefix, 0, false);
$gateway
    var v6 = [];
    try {
        var rawV6 = port.getIpv6Addresses();
        if (rawV6) {
            if (typeof rawV6.length === "number") {
                for (var j = 0; j < rawV6.length; j += 1) { v6.push(String(rawV6[j])); }
            } else {
                v6.push(String(rawV6));
            }
        }
    } catch (e2) { v6 = []; }
    return {port: $port, address: $address, prefix: $prefix, gateway: $gateway_value, ipv6: v6};
}())"""
)

_DNS_RECORD_JS = string.Template(
    """$marker(function () {
    $lookup
    var dns = null;
    try { dns = dev.getProcess("DnsServer"); } catch (e1) { dns = null; }
    if (!dns) { throw new Error("no DnsServer process on " + $want); }
    var result = {
        name: $name,
        ip: $ip,
        zone: $zone,
        ns: $ns,
        mailbox: $mailbox,
        enabled: $enabled,
        soa: $soa,
        ns_record: $ns_record,
        ip_entry: null,
        a_record: null,
        entries: null
    };
$action
    try { result.entries = dns.getEntryCount(); } catch (e9) { result.entries = null; }
    return result;
}())"""
)

_DNS_LIST_JS = string.Template(
    """$marker(function () {
    $lookup
    var dns = null;
    try { dns = dev.getProcess("DnsServer"); } catch (e1) { dns = null; }
    if (!dns) { throw new Error("no DnsServer process on " + $want); }
$body
}())"""
)

_HTTP_PAGE_JS = string.Template(
    """$marker(function () {
    $lookup
    var http = null;
    try { http = dev.getProcess("HttpServer"); } catch (e1) { http = null; }
    if (!http) { throw new Error("no HttpServer process on " + $want); }
    http.setEnable($enabled);
$content
    var result = {path: $path, enabled: $enabled, page: ""};
    try { result.enabled = !!http.isEnabled(); } catch (e2) { result.enabled = $enabled; }
    try { result.page = String(http.getPage($path)); } catch (e3) { result.page = ""; }
    return result;
}())"""
)

_PC_OUTPUT_JS = string.Template(
    """$marker(function () {
    $lookup
    var line = null;
    try { line = dev.getCommandLine(); } catch (e1) { line = null; }
    if (!line) { throw new Error("no command line on " + $want); }
    if (typeof line.getOutput !== "function") {
        throw new Error("device " + $want + " has no console output buffer");
    }
$what
}())"""
)

_LIST_LINKS_JS = string.Template(
    """$marker(function () {
    var net = ipc.network();
    var out = [];
    var seen = {};
    for (var d = 0; d < net.getDeviceCount(); d += 1) {
        var dev = net.getDeviceAt(d);
        if (!dev) { continue; }
        var portCount = 0;
        try { portCount = dev.getPortCount(); } catch (e1) { portCount = 0; }
        for (var p = 0; p < portCount; p += 1) {
            var port = null;
            try { port = dev.getPortAt(p); } catch (e2) { port = null; }
            if (!port) { continue; }
            var link = null;
            try { link = port.getLink(); } catch (e3) { link = null; }
            if (!link) { continue; }
            var p1 = null;
            var p2 = null;
            try { p1 = link.getPort1(); } catch (e4) { p1 = null; }
            try { p2 = link.getPort2(); } catch (e5) { p2 = null; }
            if (!p1 || !p2) { continue; }
            var a = "";
            var ap = "";
            var b = "";
            var bp = "";
            try { a = String(p1.getOwnerDevice().getName()); } catch (e6) { a = ""; }
            try { ap = String(p1.getName()); } catch (e7) { ap = ""; }
            try { b = String(p2.getOwnerDevice().getName()); } catch (e8) { b = ""; }
            try { bp = String(p2.getName()); } catch (e9) { bp = ""; }
            var key = a + "|" + ap + "|" + b + "|" + bp;
            var rev = b + "|" + bp + "|" + a + "|" + ap;
            if (seen[key] || seen[rev]) { continue; }
            seen[key] = true;
            var up = false;
            try { up = !!p1.isPortUp() && !!p2.isPortUp(); } catch (e10) { up = false; }
            var cable = "";
            try { cable = String(link.getConnectionType()); } catch (e11) { cable = ""; }
            out.push({a: a, a_port: ap, b: b, b_port: bp, cable: cable, up: up});
        }
    }
    return out;
}())"""
)

_LIST_PORTS_JS = string.Template(
    """$marker(function () {
    $lookup
    var ports = [];
    var count = 0;
    try { count = dev.getPortCount(); } catch (e1) { count = 0; }
    for (var i = 0; i < count; i += 1) {
        var port = null;
        try { port = dev.getPortAt(i); } catch (e2) { port = null; }
        if (!port) { continue; }
        var name = "Port" + i;
        var up = false;
        var proto = false;
        var ip = "";
        var mask = "";
        var link = "";
        try { name = String(port.getName()); } catch (e3) { name = "Port" + i; }
        try { up = !!port.isPortUp(); } catch (e4) { up = false; }
        try { proto = !!port.isProtocolUp(); } catch (e5) { proto = false; }
        try { ip = String(port.getIpAddress()); } catch (e6) { ip = ""; }
        try { mask = String(port.getSubnetMask()); } catch (e7) { mask = ""; }
        try {
            var cable = port.getLink();
            if (cable) {
                var p1 = cable.getPort1();
                var p2 = cable.getPort2();
                var other = p2;
                if (p1 && String(p1.getName()) === name &&
                        String(p1.getOwnerDevice().getName()) === String(dev.getName())) {
                    other = p2;
                } else {
                    other = p1;
                }
                if (other) {
                    link = String(other.getOwnerDevice().getName()) + " " +
                        String(other.getName());
                }
            }
        } catch (e8) { link = ""; }
        ports.push({name: name, up: up, protocol_up: proto, ip: ip, mask: mask, link: link});
    }
    return ports;
}())"""
)


# --------------------------------------------------------------------------
# Host / topology tool bodies (plain values; the MCP tools below wrap them)
# --------------------------------------------------------------------------
def _get_config(device: str, port: str | None = None) -> dict[str, Any]:
    """Read ``{name, ip, mask, dhcp, ipv6, up}`` for one port or every port."""
    if port:
        collect = f"    ports.push(readPort(dev.getPort({_lit(port)}), 0));"
    else:
        collect = (
            "    var count = 0;\n"
            "    try { count = dev.getPortCount(); } catch (e0) { count = 0; }\n"
            "    for (var p = 0; p < count; p += 1) {\n"
            "        ports.push(readPort(dev.getPortAt(p), p));\n"
            "    }"
        )
    js = _tmpl(
        _GET_CONFIG_JS,
        marker=_marker("pt_get_config"),
        lookup=_device_js(device),
        collect=collect,
    )
    ports = _eval(js, device, "pt_get_config")
    if not isinstance(ports, list):
        raise PTBridgeError(
            _api_error(f"expected a port list, got {ports!r}", device, "pt_get_config")
        )
    return {"ports": ports}


def _set_ip(
    device: str,
    port: str,
    ip: str | None,
    mask: str | None,
    gateway: str | None,
    dns: str | None,
    dhcp: bool,
) -> dict[str, Any]:
    """Set a static address, or start DHCP; return the read-back address."""
    if not dhcp and (ip is None) != (mask is None):
        raise PTBridgeError(
            "pt_set_ip: ip and mask must be given together "
            f"(got ip={ip!r}, mask={mask!r}); use dhcp=True for a leased address"
        )

    port_lit = _lit(port)
    if dhcp:
        action = (
            "    port.setDhcpClientFlag(true);\n"
            "    var dhcpClient = null;\n"
            '    try { dhcpClient = dev.getProcess("DhcpClient"); }'
            " catch (e5) { dhcpClient = null; }\n"
            "    if (!dhcpClient) {"
            ' throw new Error("no DhcpClient process on " + ' + _lit(device) + "); }\n"
            f"    dhcpClient.dhcpRun({port_lit});"
        )
    else:
        lines = ["    port.setDhcpClientFlag(false);"]
        if ip is not None and mask is not None:
            # HostPort.setIpSubnetMask takes (ip, mask) in that order — verified.
            lines.append(f"    port.setIpSubnetMask({_lit(ip)}, {_lit(mask)});")
        if gateway:
            lines.append(f"    port.setDefaultGateway({_lit(gateway)});")
        if dns:
            lines.append(f"    port.setDnsServerIp({_lit(dns)});")
        action = "\n".join(lines)

    js = _tmpl(
        _SET_IP_JS,
        marker=_marker("pt_set_ip"),
        lookup=_device_js(device),
        port=port_lit,
        action=action,
    )
    result = _eval(js, device, "pt_set_ip")
    if not isinstance(result, dict):
        raise PTBridgeError(_api_error(f"expected an address, got {result!r}", device, "pt_set_ip"))
    return result


def _set_ipv6(
    device: str,
    port: str,
    address: str,
    prefix: int,
    gateway: str | None,
) -> dict[str, Any]:
    """Enable IPv6 on ``port`` and add ``address/prefix`` (optionally a gateway)."""
    gateway_js = ""
    if gateway:
        gateway_js = f"    port.setv6DefaultGateway({_lit(gateway)});"
    js = _tmpl(
        _SET_IPV6_JS,
        marker=_marker("pt_set_ipv6"),
        lookup=_device_js(device),
        port=_lit(port),
        address=_lit(address),
        prefix=_lit(int(prefix)),
        gateway=gateway_js,
        gateway_value=_lit(gateway),
    )
    result = _eval(js, device, "pt_set_ipv6")
    if not isinstance(result, dict):
        raise PTBridgeError(
            _api_error(f"expected an address, got {result!r}", device, "pt_set_ipv6")
        )
    return result


def _dns_zone(name: str) -> str:
    """``www.g4.lab`` -> ``g4.lab``; a two-label name is its own zone."""
    labels = [label for label in str(name).split(".") if label]
    if len(labels) <= 2:
        return str(name)
    return ".".join(labels[1:])


def _dns_record(
    device: str,
    name: str,
    ip: str,
    enable: bool,
    zone: str | None,
    ns_name: str | None,
    soa: bool,
    ns: bool,
    mailbox: str | None,
    min_ttl: int,
    refresh: int,
    retry: int,
    expiry: int,
) -> dict[str, Any]:
    """Add (or remove) one A record, plus the zone's SOA/NS (PTM-04).

    ``DnsServer.addIpAddress`` alone is not enough: PT answers ``NXDOMAIN``
    until ``addARecordToNameServerDb`` runs too, and the answer is
    non-authoritative without an SOA and an NS for the zone. Both A calls are
    always made together here.
    """
    zone = zone or _dns_zone(name)
    ns_name = ns_name or device
    mailbox = mailbox or f"admin.{zone}"
    name_lit = _lit(name)
    ip_lit = _lit(ip)
    if enable:
        action = (
            f"    try {{ result.ip_entry = !!dns.addIpAddress({name_lit}, {ip_lit}); }}\n"
            "    catch (e2) { throw new Error(\"addIpAddress failed: \" + String(e2)); }\n"
            "    try { result.a_record = !!dns.addARecordToNameServerDb("
            f"{name_lit}, {ip_lit}); }}\n"
            "    catch (e3) { throw new Error(\"addARecordToNameServerDb failed: \""
            " + String(e3)); }"
        )
        if soa:
            action += (
                f"\n    dns.addSOAToNameServerDb({_lit(zone)}, {_lit(ns_name)}, "
                f"{_lit(mailbox)}, String({_lit(int(min_ttl))}), "
                f"String({_lit(int(refresh))}), String({_lit(int(retry))}), "
                f"String({_lit(int(expiry))}));"
            )
        if ns:
            action += (
                f"\n    dns.addNSRecordToNameServerDb({_lit(zone)}, {_lit(ns_name)});"
            )
    else:
        action = (
            f"    try {{ result.ip_entry = !!dns.removeIpAddress({name_lit}); }}\n"
            "    catch (e2) { throw new Error(\"removeIpAddress failed: \" + String(e2)); }\n"
            "    try { result.a_record = !!dns.removeARecordFromNameServerDb("
            f"{name_lit}); }}\n"
            "    catch (e3) { throw new Error(\"removeARecordFromNameServerDb failed: \""
            " + String(e3)); }"
        )

    js = _tmpl(
        _DNS_RECORD_JS,
        marker=_marker("pt_dns_record"),
        lookup=_device_js(device),
        want=_lit(device),
        name=name_lit,
        ip=ip_lit,
        zone=_lit(zone),
        ns=_lit(ns_name),
        mailbox=_lit(mailbox),
        enabled=_lit(bool(enable)),
        soa=_lit(bool(soa)),
        ns_record=_lit(bool(ns)),
        action=action,
    )
    result = _eval(js, device, "pt_dns_record")
    if not isinstance(result, dict):
        raise PTBridgeError(
            _api_error(f"expected a record, got {result!r}", device, "pt_dns_record")
        )
    return result


def _dns_list(device: str, name: str | None = None) -> dict[str, Any]:
    """One domain's address, or every entry the DNS process holds."""
    if name:
        body = f"    return {{name: {_lit(name)}, ip: String(dns.getIpAddOfDomain({_lit(name)}))}};"
    else:
        body = (
            "    var entries = [];\n"
            "    var count = 0;\n"
            "    try { count = dns.getEntryCount(); } catch (e2) { count = 0; }\n"
            "    for (var i = 0; i < count; i += 1) {\n"
            "        try { entries.push(String(dns.getEntryAt(i))); }"
            ' catch (e3) { entries.push(""); }\n'
            "    }\n"
            "    return {count: count, entries: entries};"
        )
    js = _tmpl(
        _DNS_LIST_JS,
        marker=_marker("pt_dns_list"),
        lookup=_device_js(device),
        want=_lit(device),
        body=body,
    )
    result = _eval(js, device, "pt_dns_list")
    if not isinstance(result, dict):
        raise PTBridgeError(_api_error(f"expected an entry, got {result!r}", device, "pt_dns_list"))
    return result


def _http_page(
    device: str, path: str, content: str | None, enable: bool
) -> dict[str, Any]:
    """Enable/disable the HTTP server, optionally replace a page, read it back."""
    content_js = ""
    if content is not None:
        content_js = f"    http.setPageContents({_lit(path)}, {_lit(content)});"
    js = _tmpl(
        _HTTP_PAGE_JS,
        marker=_marker("pt_http_page"),
        lookup=_device_js(device),
        want=_lit(device),
        path=_lit(path),
        enabled=_lit(bool(enable)),
        content=content_js,
    )
    result = _eval(js, device, "pt_http_page")
    if not isinstance(result, dict):
        raise PTBridgeError(_api_error(f"expected a page, got {result!r}", device, "pt_http_page"))
    return result


def _pc_output(device: str, *, length: bool, marker: str) -> Any:
    """Read a host's console buffer (or just its length) through ``getCommandLine``."""
    what = (
        "    return String(line.getOutput()).length;"
        if length
        else "    return String(line.getOutput());"
    )
    js = _tmpl(
        _PC_OUTPUT_JS,
        marker=_marker(marker),
        lookup=_device_js(device),
        want=_lit(device),
        what=what,
    )
    return _eval(js, device, "pt_pc_command")


def _pc_command(device: str, command: str, wait: float, ready_timeout: float) -> dict[str, Any]:
    """Type ``command`` into a host's shell and return everything it printed.

    The buffer length is taken *before* the command and the whole tail after
    ``wait`` seconds is returned, so a command that keeps printing (``ping``,
    ``tracert``) is never cut down to its first chunk.
    """
    wait_ready(device, ready_timeout)
    before = _pc_output(device, length=True, marker="pt_pc_command len")
    try:
        before_len = int(before)
    except (TypeError, ValueError):
        before_len = 0

    call_pt("exec", {"device": device, "command": command, "timeout_ms": 5000})
    if wait > 0:
        time.sleep(wait)

    full = _pc_output(device, length=False, marker="pt_pc_command output")
    text = "" if full is None else str(full)
    output = text[before_len:] if 0 <= before_len <= len(text) else text
    return {
        "device": device,
        "command": command,
        "output": output,
        "before": before_len,
        "chars": len(text),
    }


_PING_REPLY = re.compile(r"Reply from", re.IGNORECASE)
_PING_TIMEOUT = re.compile(r"Request timed out", re.IGNORECASE)
_PING_UNREACHABLE = re.compile(r"Destination host unreachable", re.IGNORECASE)
_PING_STATS = re.compile(
    r"Sent\s*=\s*(\d+)\s*,\s*Received\s*=\s*(\d+)\s*,"
    r"\s*Lost\s*=\s*(\d+)\s*\(\s*(\d+)%\s*loss\s*\)",
    re.IGNORECASE,
)


def _count_echoes(text: str) -> tuple[int, int, int]:
    """Count replies, timeouts and unreachable answers, one line at a time.

    ``Reply from <src>: Destination host unreachable.`` is *not* a reply, so each
    line is classified once — unreachable first, then a real reply, then a
    timeout.
    """
    replies = 0
    timed_out = 0
    unreachable = 0
    for line in text.splitlines():
        if _PING_UNREACHABLE.search(line):
            unreachable += 1
        elif _PING_REPLY.search(line):
            replies += 1
        elif _PING_TIMEOUT.search(line):
            timed_out += 1
    return replies, timed_out, unreachable


def _ping(src: str, dst: str, count: int, wait: float) -> dict[str, Any]:
    """Ping from a host and summarise the echo replies."""
    result = _pc_command(src, f"ping {dst}", wait=wait, ready_timeout=READY_TIMEOUT)
    text = str(result.get("output") or "")
    replies, timed_out, unreachable = _count_echoes(text)

    stats = _PING_STATS.search(text)
    if stats:
        sent, received, _lost, loss = (int(group) for group in stats.groups())
    else:
        sent = replies + timed_out + unreachable
        received = replies
        loss = round(100 * (sent - received) / sent) if sent else 0

    return {
        "src": src,
        "dst": dst,
        "count": count,
        "sent": sent,
        "received": received,
        "loss": loss,
        "replies": replies,
        "timed_out": timed_out,
        "unreachable": unreachable,
        "output": text,
    }


def _skip_boot(device: str, wait: bool, ready_timeout: float) -> dict[str, Any]:
    """Press *Skip boot* on a device, optionally waiting for its prompt."""
    result = call_pt("skip_boot", {"device": device})
    payload = dict(result) if isinstance(result, dict) else {"ok": True}
    payload.setdefault("ok", True)
    if wait:
        wait_ready(device, ready_timeout)
        payload["ready"] = True
    return payload


def _dedupe_links(links: list[Any]) -> list[dict[str, Any]]:
    """Keep one entry per physical cable, whichever end was walked first.

    The generated JS already dedupes; doing it again here keeps the contract
    ("one cable, one entry") even if a PT build returns doubles.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for item in links:
        if not isinstance(item, dict):
            continue
        a = str(item.get("a", ""))
        a_port = str(item.get("a_port", ""))
        b = str(item.get("b", ""))
        b_port = str(item.get("b_port", ""))
        if not (a and a_port and b and b_port):
            continue
        key = tuple(sorted([f"{a}|{a_port}", f"{b}|{b_port}"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "a": a,
                "a_port": a_port,
                "b": b,
                "b_port": b_port,
                "cable": str(item.get("cable", "")),
                "up": bool(item.get("up")),
            }
        )
    return out


def _list_links() -> list[dict[str, Any]]:
    """Every cable in the active workspace, one entry per physical link."""
    js = _tmpl(_LIST_LINKS_JS, marker=_marker("pt_list_links"))
    links = _eval(js, None, "pt_list_links")
    if not isinstance(links, list):
        raise PTBridgeError(
            _api_error(f"expected a link list, got {links!r}", None, "pt_list_links")
        )
    return _dedupe_links(links)


def _list_ports(device: str) -> list[dict[str, Any]]:
    """Every port on ``device`` with its link/carrier state."""
    js = _tmpl(
        _LIST_PORTS_JS,
        marker=_marker("pt_list_ports"),
        lookup=_device_js(device),
    )
    ports = _eval(js, device, "pt_list_ports")
    if not isinstance(ports, list):
        raise PTBridgeError(
            _api_error(f"expected a port list, got {ports!r}", device, "pt_list_ports")
        )
    return ports


_RAW_PREFIX = (
    "(function(){try{var __v=eval("
)
_RAW_SUFFIX = (
    ");return JSON.stringify({ok:true,value:__v});}"
    "catch(e){return JSON.stringify({ok:false,error:String(e),"
    "line:(e&&e.lineNumber)||null});}})()"
)


def _raw(js: str, max_chars: int) -> dict[str, Any]:
    """Evaluate ``js`` and return its value as a real JSON value (PTM-15).

    The snippet is wrapped so a thrown error comes back as data (with the JS
    line number when the engine reports one) instead of a raw C++ exception, and
    a huge result is cut at ``max_chars`` with ``"truncated": true``.
    """
    payload = _eval(_RAW_PREFIX + js + _RAW_SUFFIX, None, "pt_raw")

    if isinstance(payload, dict) and payload.get("ok") is False:
        line = payload.get("line")
        message = str(payload.get("error") or "unknown error")
        if line:
            message += f" at line {line}"
        raise PTBridgeError(_api_error(message, None, "pt_raw"))

    if isinstance(payload, dict) and payload.get("ok") is True:
        value = payload.get("value")
    else:
        value = payload

    try:
        rendered = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = str(value)

    if max_chars > 0 and len(rendered) > max_chars:
        return {"value": rendered[:max_chars], "truncated": True, "chars": len(rendered)}
    return {"value": value}


# --------------------------------------------------------------------------
# Observe
# --------------------------------------------------------------------------
@server.tool()
def pt_status() -> str:
    """Check the pt-mcp bridge: PT version and number of devices in the active .pkt."""
    if not config.BASE.exists():
        return _err(not_set_up(config.BASE))
    return run_op("ping")


@server.tool()
def pt_list_devices() -> str:
    """List all devices (name, type, model) and the link count in the active .pkt."""
    return run_op("list_devices")


@server.tool()
def pt_show(device: str, command: str, ready_timeout: float = READY_TIMEOUT) -> str:
    """Run a single show/exec command on a device and return the output.

    Args:
        device: device name.
        command: one exec-mode command (e.g. "show ip interface brief").
        ready_timeout: seconds to wait for the device prompt before giving up.
    """
    failure = _wait_or_error(device, ready_timeout)
    if failure:
        return failure
    return run_op("exec", {"device": device, "command": command})


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
@server.tool()
def pt_add_device(
    name: str,
    type: str | int,
    model: str,
    x: float = 100.0,
    y: float = 100.0,
    skip_boot: bool = False,
) -> str:
    """Add a device to the active workspace.

    Args:
        name: desired device name (e.g. "R1"). Empty string keeps the auto name.
        type: DeviceType name (e.g. "eRouter", or just "Router") or integer
            (0 = router). An unknown name is rejected with the valid list and a
            "Did you mean …?" suggestion instead of being sent to PT.
        model: PT model string (e.g. "2911", "2960-24TT").
        x, y: canvas coordinates.
        skip_boot: press *Skip boot* right after adding, so the device is usable
            without waiting out the IOS boot.

    Returns ``{"name": ..., "ready": true|false}`` — ``ready`` is false while the
    device still boots, which is when the next call should use ``ready_timeout``.
    """
    try:
        resolved = resolve_device_type(type)
    except ValueError as exc:
        return _err(str(exc))
    return _tool(
        "pt_add_device",
        _add_device,
        name,
        resolved,
        model,
        x,
        y,
        skip_boot,
    )


def _add_device(
    name: str,
    type_id: int,
    model: str,
    x: float,
    y: float,
    skip_boot: bool,
) -> dict[str, Any]:
    """``add_device`` (+ optional ``skip_boot``) as one tool result."""
    added = call_pt("add_device", {"name": name, "type": type_id, "model": model, "x": x, "y": y})
    payload = dict(added) if isinstance(added, dict) else {"name": name}
    if skip_boot:
        actual = str(payload.get("name") or name)
        call_pt("skip_boot", {"device": actual})
        payload["skipped_boot"] = True
    return payload


@server.tool()
def pt_remove_device(name: str) -> str:
    """Remove a device (by name) from the active workspace."""
    return run_op("remove_device", {"name": name})


@server.tool()
def pt_link(
    a: str,
    a_port: str,
    b: str,
    b_port: str,
    cable: str = "eCopperGigabitEthernet",
) -> str:
    """Create a physical link between two device ports.

    Args:
        a, a_port: first device name and port (e.g. "R1", "GigabitEthernet0/0").
        b, b_port: second device name and port.
        cable: cable name or CONNECT_TYPES int. Accepted names (case-insensitive,
            ``e`` optional): "eCopperEthernet"/"eCopperFastEthernet"/
            "eCopperGigabitEthernet", "eFiberFastEthernet"/
            "eFiberGigabitEthernet", "eSerial", "eCopperCoaxial", "straight"
            (default), "crossover", "fiber", "serial", "console", "wireless",
            "coax", "auto". An unknown name is rejected with the valid list and a
            closest match instead of being passed through to PT.
    """
    try:
        resolved = resolve_cable(cable)
    except ValueError as exc:
        return _err(str(exc))
    args = {"a": a, "aPort": a_port, "b": b, "bPort": b_port, "cable": resolved}
    return run_op("link", args)


@server.tool()
def pt_autolink(a: str, b: str) -> str:
    """Auto-connect two devices (PT picks the default cable and ports)."""
    return run_op("autolink", {"a": a, "b": b})


@server.tool()
def pt_power(
    device: str,
    on: bool = True,
    ready: bool = False,
    ready_timeout: float = READY_TIMEOUT,
) -> str:
    """Turn a device's power on (on=True) or off (on=False).

    Args:
        device: device name.
        on: True powers on, False powers off.
        ready: after powering on, wait until the device prompt is usable.
        ready_timeout: seconds to wait for that prompt when ``ready`` is true.

    A power cycle is the escape hatch for a wedged console, but it discards
    everything that was not saved into the .pkt — see the Recovery section of
    docs/setup.md before using it.
    """
    result = run_op("power", {"device": device, "on": bool(on)})
    if not on or not ready:
        return result
    failure = _wait_or_error(device, ready_timeout)
    return failure or result


@server.tool()
def pt_skip_boot(device: str, wait: bool = False, ready_timeout: float = READY_TIMEOUT) -> str:
    """Press *Skip boot* on a device so it reaches its console without the boot.

    Args:
        device: device name.
        wait: after skipping, wait until the device prompt is usable.
        ready_timeout: seconds to wait for that prompt when ``wait`` is true.

    Use it on a freshly added IOS device (or one stuck in the boot sequence);
    ``pt_add_device(..., skip_boot=True)`` does the same in one call.
    """
    return _tool("pt_skip_boot", _skip_boot, device, bool(wait), ready_timeout)


# --------------------------------------------------------------------------
# Topology
# --------------------------------------------------------------------------
@server.tool()
def pt_list_links() -> str:
    """List every cable in the active .pkt, one entry per physical link.

    Returns ``[{a, a_port, b, b_port, cable, up}]``; ``cable`` is PT's
    connection-type id as a string and ``up`` is true when both ends have
    carrier. Entries are deduped, so each cable appears once.
    """
    return _tool("pt_list_links", _list_links)


@server.tool()
def pt_list_ports(device: str) -> str:
    """List a device's ports: name, up, protocol_up, ip, mask and the link peer.

    Returns ``[{name, up, protocol_up, ip, mask, link}]``; ``link`` is
    ``"<device> <port>"`` of the other end, or ``""`` when the port is free.
    """
    return _tool("pt_list_ports", _list_ports, device)


# --------------------------------------------------------------------------
# Hosts (end devices, servers and any other non-IOS addressable port)
# --------------------------------------------------------------------------
@server.tool()
def pt_get_config(device: str, port: str | None = None) -> str:
    """Read the IP configuration of a device's ports (all ports by default).

    Args:
        device: device name (PC, server, router, …).
        port: optional single port name (e.g. "FastEthernet0").

    Returns ``{"ports": [{name, ip, mask, dhcp, ipv6, up}]}``.
    """
    return _tool("pt_get_config", _get_config, device, port)


@server.tool()
def pt_set_ip(
    device: str,
    port: str = "FastEthernet0",
    ip: str | None = None,
    mask: str | None = None,
    gateway: str | None = None,
    dns: str | None = None,
    dhcp: bool = False,
) -> str:
    """Set a host port's IPv4 address — static, or DHCP by asking the server.

    Args:
        device: device name.
        port: port name to configure (default "FastEthernet0").
        ip, mask: static address; give both or neither. PT's API takes the
            address first and the mask second.
        gateway: default gateway, when given.
        dns: DNS server address, when given.
        dhcp: request a lease instead. The static fields are ignored; PT's DHCP
            client runs asynchronously, so the returned address may still be
            0.0.0.0 — read it again with pt_get_config after a moment.

    Returns ``{"port", "ip", "mask", "dhcp"}`` read back from the device.
    """
    return _tool("pt_set_ip", _set_ip, device, port, ip, mask, gateway, dns, bool(dhcp))


@server.tool()
def pt_set_ipv6(
    device: str,
    port: str,
    address: str,
    prefix: int = 64,
    gateway: str | None = None,
) -> str:
    """Enable IPv6 on a host port and add a static address.

    Args:
        device: device name.
        port: port name (e.g. "FastEthernet0").
        address: IPv6 address, e.g. "2001:db8::10".
        prefix: prefix length (default 64).
        gateway: optional IPv6 default gateway.

    Returns ``{"port", "address", "prefix", "gateway", "ipv6"}`` read back.
    """
    return _tool(
        "pt_set_ipv6", _set_ipv6, device, port, address, int(prefix), gateway
    )


@server.tool()
def pt_dns_record(
    device: str,
    name: str,
    ip: str,
    enable: bool = True,
    zone: str | None = None,
    ns_name: str | None = None,
    soa: bool = True,
    ns: bool = True,
    mailbox: str | None = None,
    min_ttl: int = 3600,
    refresh: int = 3600,
    retry: int = 3600,
    expiry: int = 3600,
) -> str:
    """Add (or remove) an A record on a device running the DNS service.

    Both halves of a record are written: PT answers with NXDOMAIN when only
    ``addIpAddress`` ran, so ``addARecordToNameServerDb`` is always called too.
    An authoritative answer additionally needs the zone's SOA and NS records,
    which are added by default.

    Args:
        device: the DNS server's device name.
        name: fully qualified domain name (e.g. "www.g4.lab").
        ip: IPv4 address to answer with.
        enable: True adds the record, False removes it.
        zone: zone name; default drops the first label ("www.g4.lab" ->
            "g4.lab", a two-label name is its own zone).
        ns_name: authoritative name server; default is ``device``.
        soa, ns: also write the zone's SOA / NS record.
        mailbox: SOA mailbox; default ``admin.<zone>``.
        min_ttl, refresh, retry, expiry: SOA timers, in seconds.

    Returns the record as PT sees it: ``{name, ip, zone, ns, mailbox, enabled,
    soa, ns_record, ip_entry, a_record, entries}``.
    """
    return _tool(
        "pt_dns_record",
        _dns_record,
        device,
        name,
        ip,
        bool(enable),
        zone,
        ns_name,
        bool(soa),
        bool(ns),
        mailbox,
        int(min_ttl),
        int(refresh),
        int(retry),
        int(expiry),
    )


@server.tool()
def pt_dns_list(device: str, name: str | None = None) -> str:
    """Inspect a DNS server: one domain's address, or every entry it holds.

    Args:
        device: the DNS server's device name.
        name: optional domain; ``{"name", "ip"}`` when given, otherwise
            ``{"count", "entries"}``.
    """
    return _tool("pt_dns_list", _dns_list, device, name)


@server.tool()
def pt_http_page(
    device: str,
    path: str = "index.html",
    content: str | None = None,
    enable: bool = True,
) -> str:
    """Enable a device's HTTP server, optionally replacing one page, and read it.

    Args:
        device: device name (a server, router, … with the HTTP process).
        path: page path (default "index.html").
        content: HTML to store at ``path``; omitted keeps the current page.
        enable: start (True, default) or stop (False) the HTTP service.

    Returns ``{"path", "enabled", "page"}`` with the page as stored.
    """
    return _tool("pt_http_page", _http_page, device, path, content, bool(enable))


@server.tool()
def pt_pc_command(device: str, command: str, wait: float = 10, ready_timeout: float = 45) -> str:
    """Type a command into a host's shell and return everything it printed.

    For PCs, servers and other devices without IOS (their console is a command
    prompt, not a CLI). The command goes through the bridge's ``exec`` op, which
    is raw for such devices.

    Args:
        device: device name.
        command: shell command, e.g. "ping 192.168.10.1" or "ipconfig /all".
        wait: seconds to let the output accumulate before reading it (default 10).
        ready_timeout: seconds to wait for the host prompt first.

    Returns ``{"device", "command", "output", "before", "chars"}``; ``output`` is
    the whole new console text, not just its first chunk.
    """
    return _tool(
        "pt_pc_command",
        _pc_command,
        device,
        command,
        float(wait),
        float(ready_timeout),
    )


@server.tool()
def pt_ping(src: str, dst: str, count: int = 4, wait: float = 10) -> str:
    """Ping from a host and summarise the result.

    Args:
        src: source host device name (PC/server shell).
        dst: target address or host name.
        count: advisory only — PT's PC ping always sends exactly four echoes;
            the value is reported back, not applied.
        wait: seconds to wait for the replies (default 10).

    Returns ``{"src", "dst", "count", "sent", "received", "loss", "replies",
    "timed_out", "unreachable", "output"}`` with ``loss`` as a percentage.
    """
    return _tool("pt_ping", _ping, src, dst, int(count), float(wait))


# --------------------------------------------------------------------------
# Change / escape
# --------------------------------------------------------------------------
@server.tool()
def pt_configure(
    device: str,
    commands: str | list[str],
    mode: str = "auto",
    keep_session: bool = False,
    stop_on_error: bool = True,
    dry_run: bool = False,
    ready_timeout: float = READY_TIMEOUT,
) -> str:
    """Send IOS CLI commands to a device and return the terminal output/prompt.

    Args:
        device: device name.
        commands: newline-separated string, or a list of command strings. Blank
            lines and '!' comments are dropped; nothing else is rewritten here.
        mode: how to reach the right CLI mode — ``"auto"`` (default: inspect the
            prompt and do the right thing), ``"raw"`` (send exactly these lines),
            ``"exec"`` (exec commands only: enable if needed, never config) or
            ``"config"`` (enable + configure terminal, end unless keep_session).
            Devices without IOS (PC, server, printer, ...) always get raw.
        keep_session: do not append ``end`` — leave the device in config mode for
            the next call.
        stop_on_error: stop the block at the first rejected/timed-out line
            (default true).
        dry_run: return the lines that *would* be sent, without sending them.
        ready_timeout: seconds to wait for the device prompt before giving up.
    """
    try:
        lines = normalize_commands(commands)
    except ValueError as exc:
        return _err(str(exc))

    args: dict[str, Any] = {
        "device": device,
        "lines": lines,
        "mode": mode,
        "keep_session": bool(keep_session),
        "stop_on_error": bool(stop_on_error),
    }

    if dry_run:
        try:
            plan = call_pt("plan", args)
        except PTBridgeError as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything to the client
            return _err(f"Unexpected error calling PT bridge: {exc}")
        payload: dict[str, Any] = {"dry_run": True}
        if isinstance(plan, dict):
            payload.update(plan)
        else:  # pragma: no cover - the bridge always returns an object
            payload["lines"] = plan
        return _ok(payload)

    failure = _wait_or_error(device, ready_timeout)
    if failure:
        return failure
    return run_op("configure", args)


@server.tool()
def pt_save(path: str = "") -> str:
    """Save the active .pkt to a path (default: <base>/saved/<timestamp>.pkt)."""
    if not path:
        config.SAVED.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = str(config.SAVED / f"{stamp}.pkt")
    return run_op("save", {"path": path})


@server.tool()
def pt_raw(js: str, max_chars: int = 20000) -> str:
    """Escape hatch: evaluate arbitrary JS inside the PT Script Engine.

    The script has access to the global ``ipc`` object; use with care. Example:
        ipc.network().getDeviceCount()

    The value comes back as a real JSON value when it is serialisable (no second
    parse on the client side). A thrown error is returned as
    ``{"ok": false, "error": "PT API error: …"}` with the JS line number when the
    engine reports one. Results longer than ``max_chars`` are cut and flagged
    with ``"truncated": true``.
    """
    return _tool("pt_raw", _raw, js, int(max_chars))


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main() -> None:
    """Run the MCP server over stdio (the only transport this project ships)."""
    server.run("stdio")


if __name__ == "__main__":
    # Only runs as `python -m pt_mcp.server`: the console script and the tests go
    # through the imported main(), which tests/test_protocol.py covers.
    main()  # pragma: no cover - true script entry point

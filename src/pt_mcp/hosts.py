"""Host tool bodies: addresses, DNS, HTTP, console and ping (PTM-03/04/12).

Packet Tracer's API is only reachable from inside PT, so each of these builds an
ES5 snippet (:mod:`pt_mcp.snippets`), ships it through ``eval`` and reads the
unwrap back in :func:`pt_mcp.eval._eval`. Nothing here renders JSON: the MCP
tools in ``pt_mcp.tools.hosts`` do that with :func:`pt_mcp.eval._tool`.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .eval import _api_error, _device_js, _eval, _lit, _marker, _tmpl
from .protocol import PTBridgeError, call_pt
from .readiness import READY_TIMEOUT, wait_ready
from .snippets import (
    _DNS_LIST_JS,
    _DNS_RECORD_JS,
    _GET_CONFIG_JS,
    _HTTP_PAGE_JS,
    _PC_OUTPUT_JS,
    _SET_IP_JS,
    _SET_IPV6_JS,
)


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
        marker=_marker("get_config"),
        lookup=_device_js(device),
        collect=collect,
    )
    ports = _eval(js, device, "get_config")
    if not isinstance(ports, list):
        raise PTBridgeError(
            _api_error(f"expected a port list, got {ports!r}", device, "get_config")
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
            "set_ip: ip and mask must be given together "
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
        marker=_marker("set_ip"),
        lookup=_device_js(device),
        port=port_lit,
        action=action,
    )
    result = _eval(js, device, "set_ip")
    if not isinstance(result, dict):
        raise PTBridgeError(_api_error(f"expected an address, got {result!r}", device, "set_ip"))
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
        marker=_marker("set_ipv6"),
        lookup=_device_js(device),
        port=_lit(port),
        address=_lit(address),
        prefix=_lit(int(prefix)),
        gateway=gateway_js,
        gateway_value=_lit(gateway),
    )
    result = _eval(js, device, "set_ipv6")
    if not isinstance(result, dict):
        raise PTBridgeError(
            _api_error(f"expected an address, got {result!r}", device, "set_ipv6")
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
        # The DNS service is off by default on a Server-PT; records alone are
        # inert until it is enabled (PT still answers NXDOMAIN/timeouts).
        action = (
            "    dns.setEnable(true);\n"
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
            f"{name_lit}, {ip_lit}); }}\n"
            "    catch (e3) { throw new Error(\"removeARecordFromNameServerDb failed: \""
            " + String(e3)); }"
        )

    js = _tmpl(
        _DNS_RECORD_JS,
        marker=_marker("dns_record"),
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
    result = _eval(js, device, "dns_record")
    if not isinstance(result, dict):
        raise PTBridgeError(
            _api_error(f"expected a record, got {result!r}", device, "dns_record")
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
            "        try {\n"
            "            var entry = dns.getEntryAt(i);\n"
            "            entries.push({name: String(entry.first), ip: String(entry.second)});\n"
            "        } catch (e3) { entries.push({name: \"\", ip: \"\"}); }\n"
            "    }\n"
            "    return {count: count, entries: entries};"
        )
    js = _tmpl(
        _DNS_LIST_JS,
        marker=_marker("dns_list"),
        lookup=_device_js(device),
        want=_lit(device),
        body=body,
    )
    result = _eval(js, device, "dns_list")
    if not isinstance(result, dict):
        raise PTBridgeError(_api_error(f"expected an entry, got {result!r}", device, "dns_list"))
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
        marker=_marker("http_page"),
        lookup=_device_js(device),
        want=_lit(device),
        path=_lit(path),
        enabled=_lit(bool(enable)),
        content=content_js,
    )
    result = _eval(js, device, "http_page")
    if not isinstance(result, dict):
        raise PTBridgeError(_api_error(f"expected a page, got {result!r}", device, "http_page"))
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
    return _eval(js, device, "pc_command")


def _pc_command(device: str, command: str, wait: float, ready_timeout: float) -> dict[str, Any]:
    """Type ``command`` into a host's shell and return everything it printed.

    The buffer length is taken *before* the command and the whole tail after
    ``wait`` seconds is returned, so a command that keeps printing (``ping``,
    ``tracert``) is never cut down to its first chunk.
    """
    wait_ready(device, ready_timeout)
    before = _pc_output(device, length=True, marker="pc_command len")
    try:
        before_len = int(before)
    except (TypeError, ValueError):
        before_len = 0

    call_pt("exec", {"device": device, "command": command, "timeout_ms": 5000})
    if wait > 0:
        time.sleep(wait)

    full = _pc_output(device, length=False, marker="pc_command output")
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

"""Host tools: addresses, DNS and HTTP services, console commands and ping."""

from __future__ import annotations

from ..eval import _tool
from ..hosts import (
    _dns_list,
    _dns_record,
    _http_page,
    _pc_command,
    _ping,
    _set_ip,
    _set_ipv6,
)
from ..registry import server

# --------------------------------------------------------------------------
# Hosts (end devices, servers and any other non-IOS addressable port)
# --------------------------------------------------------------------------


@server.tool()
def set_ip(
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
            0.0.0.0 — read it again with get_config after a moment.

    Returns ``{"port", "ip", "mask", "dhcp"}`` read back from the device.
    """
    return _tool("set_ip", _set_ip, device, port, ip, mask, gateway, dns, bool(dhcp))


@server.tool()
def set_ipv6(
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
        "set_ipv6", _set_ipv6, device, port, address, int(prefix), gateway
    )


@server.tool()
def dns_record(
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
        "dns_record",
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
def dns_list(device: str, name: str | None = None) -> str:
    """Inspect a DNS server: one domain's address, or every entry it holds.

    Args:
        device: the DNS server's device name.
        name: optional domain; ``{"name", "ip"}`` when given, otherwise
            ``{"count", "entries"}``.
    """
    return _tool("dns_list", _dns_list, device, name)


@server.tool()
def http_page(
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
    return _tool("http_page", _http_page, device, path, content, bool(enable))


@server.tool()
def pc_command(device: str, command: str, wait: float = 10, ready_timeout: float = 45) -> str:
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
        "pc_command",
        _pc_command,
        device,
        command,
        float(wait),
        float(ready_timeout),
    )


@server.tool()
def ping(src: str, dst: str, count: int = 4, wait: float = 10) -> str:
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
    return _tool("ping", _ping, src, dst, int(count), float(wait))

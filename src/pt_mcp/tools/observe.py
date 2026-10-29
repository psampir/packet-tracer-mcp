"""Read-only tools: status, device list, cables, ports and port configuration."""

from __future__ import annotations

from .. import config
from ..eval import _tool
from ..hosts import _get_config
from ..protocol import _err, not_set_up, run_op
from ..registry import server
from ..topology import _list_links, _list_ports

# --------------------------------------------------------------------------
# Observe
# --------------------------------------------------------------------------


@server.tool()
def status() -> str:
    """Check the packet-tracer-mcp bridge: PT version and number of devices in the active .pkt."""
    if not config.BASE.exists():
        return _err(not_set_up(config.BASE))
    return run_op("ping")


@server.tool()
def list_devices() -> str:
    """List all devices (name, type, model) and the link count in the active .pkt."""
    return run_op("list_devices")


# --------------------------------------------------------------------------
# Topology
# --------------------------------------------------------------------------


@server.tool()
def list_links() -> str:
    """List every cable in the active .pkt, one entry per physical link.

    Returns ``[{a, a_port, b, b_port, cable, up}]``; ``cable`` is PT's
    connection-type id as a string and ``up`` is true when both ends have
    carrier. Entries are deduped, so each cable appears once.
    """
    return _tool("list_links", _list_links)


@server.tool()
def list_ports(device: str) -> str:
    """List a device's ports: name, up, protocol_up, ip, mask and the link peer.

    Returns ``[{name, up, protocol_up, ip, mask, link}]``; ``link`` is
    ``"<device> <port>"`` of the other end, or ``""`` when the port is free.
    """
    return _tool("list_ports", _list_ports, device)


# --------------------------------------------------------------------------
# Hosts (end devices, servers and any other non-IOS addressable port)
# --------------------------------------------------------------------------


@server.tool()
def get_config(device: str, port: str | None = None) -> str:
    """Read the IP configuration of a device's ports (all ports by default).

    Args:
        device: device name (PC, server, router, …).
        port: optional single port name (e.g. "FastEthernet0").

    Returns ``{"ports": [{name, ip, mask, dhcp, ipv6, up}]}``.
    """
    return _tool("get_config", _get_config, device, port)

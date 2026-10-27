"""MCP server exposing Cisco Packet Tracer to an AI agent over stdio.

Eleven tools, in three groups:

* **observe** — :func:`pt_status`, :func:`pt_list_devices`, :func:`pt_show`
* **build** — :func:`pt_add_device`, :func:`pt_remove_device`, :func:`pt_link`,
  :func:`pt_autolink`, :func:`pt_power`
* **change / escape** — :func:`pt_configure`, :func:`pt_save`, :func:`pt_raw`

They all funnel into :func:`pt_mcp.protocol.call_pt`, i.e. one JSON file in
``<PT_MCP_HOME>/jobs`` and one out of ``<PT_MCP_HOME>/results``; the ops are
implemented by ``bridge/bridge.js`` running inside Packet Tracer, which always
acts on the **active** ``.pkt`` file.

Every tool returns a **JSON string** — a result, or ``{"ok": false, "error"}``
so an LLM client always gets something it can act on instead of a traceback.

SDK note: ``mcp`` 2.x renamed ``FastMCP`` to ``MCPServer``
(``from mcp.server.mcpserver import MCPServer``); the import below falls back to
the v1 ``FastMCP`` name so either major works. Transport: stdio only —
``main()`` starts the server with ``server.run("stdio")`` and prints nothing, so
the stream stays clean for the JSON-RPC handshake.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _MCPServerType
except ImportError:  # pragma: no cover - only hit with mcp < 2
    from mcp.server.fastmcp import FastMCP as _MCPServerType

from . import __version__, config
from .commands import normalize_commands
from .protocol import _err, not_set_up, run_op

_INSTRUCTIONS = f"""\
Packet Tracer bridge (pt-mcp {__version__}). Packet Tracer must be running with a
.pkt file open and the com.pawel.ptmcp Script Module started; every tool acts on
the ACTIVE file.

Typical flow:
  1. pt_status()            -> PT version + device count (also proves the bridge works)
  2. pt_list_devices()      -> names, types, models, link count
  3. pt_add_device(...)     -> name, type (eDeviceType name or int), model, x, y
  4. pt_link(...) / pt_autolink(...)
  5. pt_configure(...)      -> IOS CLI; friendly names, blanks and '!' handled
  6. pt_show(...)           -> one show/exec command
  7. pt_power(...) / pt_save(...)

All results are JSON strings; failures come back as {{"ok": false, "error": ...}}.
If a call times out, Packet Tracer is not answering: check that PT is running, a
.pkt is open, the Script Module is Started (Extensions -> Scripting ->
Configure PT Script Modules...) and, if the file watcher is silent, click
'Run File' to force a rescan. pt_raw(js) evaluates arbitrary JS in the PT engine
and is an escape hatch, not a normal step.
"""

server = _MCPServerType("packet-tracer-mcp", instructions=_INSTRUCTIONS)


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
def pt_show(device: str, command: str) -> str:
    """Run a single show/exec command on a device and return the output."""
    return run_op("show", {"device": device, "command": command})


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
) -> str:
    """Add a device to the active workspace.

    Args:
        name: desired device name (e.g. "R1"). Empty string keeps the auto name.
        type: DeviceType name (e.g. "eRouter") or integer (0 = router).
        model: PT model string (e.g. "2911", "2960-24TT").
        x, y: canvas coordinates.
    """
    args: dict[str, Any] = {"name": name, "type": type, "model": model, "x": x, "y": y}
    return run_op("add_device", args)


@server.tool()
def pt_remove_device(name: str) -> str:
    """Remove a device (by name) from the active workspace."""
    return run_op("remove_device", {"name": name})


@server.tool()
def pt_link(
    dev_a: str,
    port_a: str,
    dev_b: str,
    port_b: str,
    cable: str = "eCopperGigabitEthernet",
) -> str:
    """Create a physical link between two device ports.

    Args:
        dev_a, port_a: first device name and port (e.g. "R1", "GigabitEthernet0/0").
        dev_b, port_b: second device name and port.
        cable: cable name or CONNECT_TYPES int. Friendly names are mapped:
            "eCopperGigabitEthernet"/"copper"/"ethernet" -> ETHERNET_STRAIGHT (8100),
            "crossover"/"cross" -> 8101, "fiber" -> 8103, "serial" -> 8106,
            "console" -> 8108, "wireless" -> 8109, "coaxial"/"coax" -> 8110,
            "auto" -> 8107.
    """
    args = {"a": dev_a, "aPort": port_a, "b": dev_b, "bPort": port_b, "cable": cable}
    return run_op("link", args)


@server.tool()
def pt_autolink(dev_a: str, dev_b: str) -> str:
    """Auto-connect two devices (PT picks the default cable and ports)."""
    return run_op("autolink", {"a": dev_a, "b": dev_b})


@server.tool()
def pt_power(device: str, on: bool = True) -> str:
    """Turn a device's power on (on=True) or off (on=False)."""
    return run_op("power", {"device": device, "on": bool(on)})


# --------------------------------------------------------------------------
# Change / escape
# --------------------------------------------------------------------------
@server.tool()
def pt_configure(device: str, commands: str | list[str]) -> str:
    """Send IOS CLI commands to a device and return the terminal output/prompt.

    Args:
        device: device name.
        commands: newline-separated string, or a list of command strings.
            Blank lines and '!' comments are dropped. 'enable' and
            'configure terminal' are prepended if missing; 'end' is appended
            if missing.
    """
    try:
        lines = normalize_commands(commands)
    except ValueError as exc:
        return _err(str(exc))
    return run_op("configure", {"device": device, "lines": lines})


@server.tool()
def pt_save(path: str = "") -> str:
    """Save the active .pkt to a path (default: <base>/saved/<timestamp>.pkt)."""
    if not path:
        config.SAVED.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = str(config.SAVED / f"{stamp}.pkt")
    return run_op("save", {"path": path})


@server.tool()
def pt_raw(js: str) -> str:
    """Escape hatch: evaluate arbitrary JS inside the PT Script Engine.

    Returns ``{"value": "<stringified result>"}``. The script has access to the
    global ``ipc`` object; use with care. Example:
        ipc.network().getDeviceCount()
    """
    return run_op("eval", {"js": js})


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main() -> None:
    """Run the MCP server over stdio (the only transport this project ships)."""
    server.run("stdio")


if __name__ == "__main__":
    main()

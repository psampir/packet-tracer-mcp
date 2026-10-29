"""The one MCP server object and the instructions it advertises.

``mcp`` 2.x renamed ``FastMCP`` to ``MCPServer``
(``from mcp.server.mcpserver import MCPServer``); the import below falls back to
the v1 ``FastMCP`` name so either major works.

Every other module gets this single ``server`` instance from here, so a tool only
has to be decorated with ``@server.tool()`` to be registered. This module must
not import the tool modules: they import it.
"""

from __future__ import annotations

try:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _MCPServerType
except ImportError:  # pragma: no cover - only hit with mcp < 2
    from mcp.server.fastmcp import FastMCP as _MCPServerType

from . import __version__

_INSTRUCTIONS = f"""\
Packet Tracer bridge (packet-tracer-mcp {__version__}). Packet Tracer must be running with a
.pkt file open and the com.psampir.packet-tracer-mcp Script Module started; every tool acts on
the ACTIVE file.

Typical flow:
  1. status()            -> PT version + device count (also proves the bridge works)
  2. list_devices()      -> names, types, models, link count
  3. add_device(...)     -> name, type (eDeviceType name or int), model, x, y
  4. link(a, a_port, b, b_port) / autolink(a, b)
  5. configure(...)      -> IOS CLI; mode=auto|raw|exec|config, dry_run=True to preview
  6. show(...)           -> one show/exec command
  7. power(...) / save(...)

Hosts and topology (no IOS needed, all server-side over the bridge's eval op):
  get_config, set_ip, set_ipv6, dns_record, dns_list,
  http_page, pc_command, ping, list_links, list_ports,
  skip_boot.

Commands are wrapped for the mode the device is actually in (enable / configure
terminal / end), and a PC's shell gets no wrappers at all. Every call waits for
the device's prompt first (ready_timeout, default 45 s).

All results are JSON strings; failures come back as {{"ok": false, "error": ...}}.
If a call times out, Packet Tracer is not answering: check that PT is running, a
.pkt is open, the Script Module is Started (Extensions -> Scripting ->
Configure PT Script Modules...) and, if the file watcher is silent, click
'Run File' to force a rescan. raw(js) evaluates arbitrary JS in the PT engine
and is an escape hatch, not a normal step.
"""


server = _MCPServerType("packet-tracer-mcp", instructions=_INSTRUCTIONS)


__all__ = ["server"]

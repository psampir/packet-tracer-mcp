"""packet-tracer-mcp — MCP server for Cisco Packet Tracer's Script Module IPC bridge.

Packet Tracer exposes an in-process JavaScript API (the *Script Module* engine),
but no socket an outside process could connect to. This package bridges that gap
with a **file-drop transport**: the server writes a JSON job into
``<PT_MCP_HOME>/jobs``, the ``bridge/bridge.js`` script — pasted into a PT Script
Module and started with PT — performs the op and writes ``<PT_MCP_HOME>/results``.
No networking is involved, and the bridge acts on whichever ``.pkt`` file is
currently active in Packet Tracer.

Modules:

* :mod:`pt_mcp.config` — paths (``PT_MCP_HOME``), timeout and the saved/ dir.
* :mod:`pt_mcp.protocol` — the transport: :func:`call_pt`, :class:`PTBridgeError`.
* :mod:`pt_mcp.commands` — :func:`normalize_commands` for ``configure``.
* :mod:`pt_mcp.server` — the twenty-two MCP tools and the stdio entry point.
"""

from __future__ import annotations

__version__ = "0.3.0"

__all__ = ["__version__"]

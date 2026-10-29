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
* :mod:`pt_mcp.registry` — the one MCP server instance and its instructions.
* :mod:`pt_mcp.readiness` — :func:`wait_ready`: real waiting for a usable prompt.
* :mod:`pt_mcp.tables` — device-type and cable names, validated before the call.
* :mod:`pt_mcp.eval` — the ``eval`` op, its snippet helpers, the ``_tool`` wrapper.
* :mod:`pt_mcp.snippets` — the ES5 templates the host and topology tools ship.
* :mod:`pt_mcp.hosts`, :mod:`pt_mcp.topology`, :mod:`pt_mcp.raw` — the tool bodies.
* :mod:`pt_mcp.tools` — the twenty-two MCP tools, registered at import time.
* :mod:`pt_mcp.server` — entry point: re-exports them and defines ``main()``.
"""

from __future__ import annotations

__version__ = "0.3.1"

__all__ = ["__version__"]

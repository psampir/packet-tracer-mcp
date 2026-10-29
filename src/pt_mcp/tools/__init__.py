"""The twenty-two MCP tools, grouped by what they do.

Importing this package — or any of its modules — is what registers every tool on
:data:`pt_mcp.registry.server`: each function carries ``@server.tool()``, which
runs at import time. ``pt_mcp.server`` imports them for that side effect and
re-exports the functions by name.
"""

from __future__ import annotations

from . import build, cli, hosts, observe, raw

__all__ = ["build", "cli", "hosts", "observe", "raw"]

"""MCP server exposing Cisco Packet Tracer to an AI agent over stdio.

Twenty-two tools, in five groups:

* **observe** — :func:`status`, :func:`list_devices`, :func:`show`
* **build** — :func:`add_device`, :func:`remove_device`, :func:`link`,
  :func:`autolink`, :func:`power`, :func:`skip_boot`
* **topology** — :func:`list_links`, :func:`list_ports`
* **hosts** — :func:`get_config`, :func:`set_ip`, :func:`set_ipv6`,
  :func:`dns_record`, :func:`dns_list`, :func:`http_page`,
  :func:`pc_command`, :func:`ping`
* **change / escape** — :func:`configure`, :func:`save`, :func:`raw`

They all funnel into :func:`pt_mcp.protocol.call_pt`, i.e. one JSON file in
``<PT_MCP_HOME>/jobs`` and one out of ``<PT_MCP_HOME>/results``; the ops are
implemented by ``bridge/bridge.js`` running inside Packet Tracer, which always
acts on the **active** ``.pkt`` file.

This module is the entry point only. The work lives in:

* :mod:`pt_mcp.registry` — the one :data:`server` instance and its instructions;
* :mod:`pt_mcp.readiness` — :func:`wait_ready`, the real waiting the bridge
  cannot do (PTM-01);
* :mod:`pt_mcp.tables` — device-type and cable names, validated before the call;
* :mod:`pt_mcp.eval` — the ``eval`` op, its snippet helpers and the ``_tool``
  JSON wrapper;
* :mod:`pt_mcp.snippets` — the ES5 templates the host and topology tools ship;
* :mod:`pt_mcp.hosts`, :mod:`pt_mcp.topology`, :mod:`pt_mcp.raw` — the tool
  bodies, implemented *server-side on top of the existing ``eval`` op*
  (PTM-03/04/12), so the installed Script Module keeps working unchanged;
* :mod:`pt_mcp.tools` — the twenty-two tools themselves, registered with
  ``@server.tool()`` when the package is imported. They are re-exported here, so
  ``pt_mcp.server.status(...)`` and the rest stay the public surface.

Every tool returns a **JSON string** — a result, or ``{"ok": false, "error"}``
so an LLM client always gets something it can act on instead of a traceback.

Transport: stdio only — ``main()`` starts the server with
``server.run("stdio")`` and prints nothing, so the stream stays clean for the
JSON-RPC handshake. ``main(["--doctor"])`` prints the mailbox resolution
(:mod:`pt_mcp.doctor`) instead of starting anything.
"""

from __future__ import annotations

import argparse
import sys

# Importing the tool modules is what registers the twenty-two tools on
# ``server``; the names below are re-exported for callers and tests.
from . import __version__, config, doctor
from .eval import _api_error, _eval  # noqa: F401 - re-exported with the tools
from .readiness import READY_POLL_INTERVAL, READY_TIMEOUT, wait_ready  # noqa: F401
from .registry import server
from .tables import resolve_cable, resolve_device_type  # noqa: F401
from .tools.build import add_device, autolink, link, power, remove_device, skip_boot
from .tools.cli import configure, save, show
from .tools.hosts import (
    dns_list,
    dns_record,
    http_page,
    pc_command,
    ping,
    set_ip,
    set_ipv6,
)
from .tools.observe import get_config, list_devices, list_links, list_ports, status
from .tools.raw import raw

__all__ = [
    "add_device",
    "autolink",
    "configure",
    "dns_list",
    "dns_record",
    "get_config",
    "http_page",
    "link",
    "list_devices",
    "list_links",
    "list_ports",
    "main",
    "pc_command",
    "ping",
    "power",
    "raw",
    "remove_device",
    "save",
    "server",
    "set_ip",
    "set_ipv6",
    "show",
    "skip_boot",
    "status",
]


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> None:
    """Run the MCP server over stdio, or describe the mailbox with ``--doctor``."""
    parser = argparse.ArgumentParser(
        prog="packet-tracer-mcp",
        description="MCP server for Cisco Packet Tracer's Script Module bridge.",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="print the resolved mailbox, its candidates and the bridge's last "
        "answer, then exit (non-zero when no mailbox exists yet)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.doctor:
        raise SystemExit(doctor.main())

    if len(config.MAILBOX.live) > 1:
        print(
            "packet-tracer-mcp: several live mailboxes: "
            + ", ".join(str(path) for path in config.MAILBOX.live)
            + f"; using {config.MAILBOX.path}. Set PT_MCP_HOME to choose.",
            file=sys.stderr,
        )
    server.run("stdio")


if __name__ == "__main__":
    # Only runs as `python -m pt_mcp.server`: the console script and the tests go
    # through the imported main(), which tests/test_protocol.py covers.
    main()  # pragma: no cover - true script entry point

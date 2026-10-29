"""Cable and port inventory, read from the PT API (PTM-03/12).

The workspace is walked inside PT and the answer comes back through ``eval``; the
Python side only dedupes and type-checks it.
"""

from __future__ import annotations

from typing import Any

from .eval import _api_error, _device_js, _eval, _marker, _tmpl
from .protocol import PTBridgeError
from .snippets import _LIST_LINKS_JS, _LIST_PORTS_JS


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
    js = _tmpl(_LIST_LINKS_JS, marker=_marker("list_links"))
    links = _eval(js, None, "list_links")
    if not isinstance(links, list):
        raise PTBridgeError(
            _api_error(f"expected a link list, got {links!r}", None, "list_links")
        )
    return _dedupe_links(links)


def _list_ports(device: str) -> list[dict[str, Any]]:
    """Every port on ``device`` with its link/carrier state."""
    js = _tmpl(
        _LIST_PORTS_JS,
        marker=_marker("list_ports"),
        lookup=_device_js(device),
    )
    ports = _eval(js, device, "list_ports")
    if not isinstance(ports, list):
        raise PTBridgeError(
            _api_error(f"expected a port list, got {ports!r}", device, "list_ports")
        )
    return ports

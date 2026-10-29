"""The ``raw`` escape hatch: arbitrary JS inside the PT Script Engine."""

from __future__ import annotations

from ..eval import _tool
from ..raw import _raw
from ..registry import server

# --------------------------------------------------------------------------
# Escape hatch
# --------------------------------------------------------------------------


@server.tool()
def raw(js: str, max_chars: int = 20000) -> str:
    """Escape hatch: evaluate arbitrary JS inside the PT Script Engine.

    The script has access to the global ``ipc`` object; use with care. Example:
        ipc.network().getDeviceCount()

    The value comes back as a real JSON value when it is serialisable (no second
    parse on the client side). A thrown error is returned as
    ``{"ok": false, "error": "PT API error: …"}` with the JS line number when the
    engine reports one. Results longer than ``max_chars`` are cut and flagged
    with ``"truncated": true``.
    """
    return _tool("raw", _raw, js, int(max_chars))

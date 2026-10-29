"""Running generated ES5 inside Packet Tracer through the bridge's ``eval`` op.

The bridge answers ``{"value": safeStringify(result)}``, i.e. a JSON string
holding JSON, and the transport adds its own layer; :func:`_eval` unwraps both.
Every failure becomes a :class:`PTBridgeError` that names the device and the tool
(PTM-11). The marker comment on each snippet keeps ``raw`` and the fake bridge
honest about which tool produced a script.
"""

from __future__ import annotations

import json
import string
from typing import Any

from .protocol import PTBridgeError, _err, _ok, call_pt


def _tool(name: str, fn: Any, *args: Any) -> str:
    """Run a tool body, rendering any failure as the usual ``ok: false`` JSON.

    Every new tool's body returns a plain Python value; this wrapper is the one
    place that turns a :class:`PTBridgeError` (or a surprise) into the JSON
    string an LLM client can act on.
    """
    try:
        return _ok(fn(*args))
    except PTBridgeError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001 - surface anything to the client
        return _err(f"Unexpected error in {name}: {exc}")


def _lit(value: Any) -> str:
    """Render ``value`` as a JavaScript literal (JSON is a subset of ES5)."""
    return json.dumps(value, ensure_ascii=False)


def _marker(name: str) -> str:
    """The comment that identifies a generated snippet in ``raw``/logs."""
    return f"/* packet-tracer-mcp: {name} */"


def _tmpl(template: string.Template, **values: str) -> str:
    """Substitute already-encoded (:func:`_lit` / code) values into ``template``."""
    return template.substitute(values)


def _api_error(message: str, device: str | None, op: str) -> str:
    """Prefix a failure with ``PT API error:`` and add its context (PTM-11)."""
    context = f" (op={op}" + (f", device={device}" if device else "") + ")"
    if message.startswith("PT API error:"):
        return message + context
    return f"PT API error: {message}{context}"


def _eval(js: str, device: str | None = None, op: str = "eval") -> Any:
    """Evaluate ``js`` inside PT and return the unwrapped ``value``.

    The bridge answers ``{"value": "<json>"}`` (``safeStringify`` of whatever the
    script returned), and the transport adds its own JSON layer, so the value is
    parsed twice while it is still a string. Every failure becomes a
    :class:`PTBridgeError` that names the device and the tool (PTM-11).
    """
    try:
        result = call_pt("eval", {"js": js})
    except PTBridgeError as exc:
        raise PTBridgeError(_api_error(str(exc), device, op)) from exc

    if not isinstance(result, dict) or "value" not in result:
        raise PTBridgeError(
            _api_error(f"unexpected eval result (no 'value'): {result!r}", device, op)
        )

    value: Any = result["value"]
    for _ in range(2):
        if not isinstance(value, str):
            break
        try:
            value = json.loads(value)
        except ValueError:
            break
    return value


_DEVICE_LOOKUP = string.Template(
    """
    var net = ipc.network();
    var dev = null;
    for (var scan = 0; scan < net.getDeviceCount(); scan += 1) {
        var candidate = net.getDeviceAt(scan);
        if (candidate && String(candidate.getName()) === $want) {
            dev = candidate;
            break;
        }
    }
    if (dev === null) { throw new Error("device not found: " + $want); }
"""
)


def _device_js(device: str) -> str:
    """ES5 that finds ``device`` in the active workspace or throws."""
    return _DEVICE_LOOKUP.substitute(want=_lit(device))

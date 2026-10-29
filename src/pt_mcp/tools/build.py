"""Workspace-building tools: add/remove a device, link, power, skip boot."""

from __future__ import annotations

from typing import Any

from ..eval import _tool
from ..hosts import _skip_boot
from ..protocol import _err, call_pt, run_op
from ..readiness import READY_TIMEOUT, _wait_or_error
from ..registry import server
from ..tables import resolve_cable, resolve_device_type

# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------


@server.tool()
def add_device(
    name: str,
    type: str | int,
    model: str,
    x: float = 100.0,
    y: float = 100.0,
    skip_boot: bool = False,
) -> str:
    """Add a device to the active workspace.

    Args:
        name: desired device name (e.g. "R1"). Empty string keeps the auto name.
        type: DeviceType name (e.g. "eRouter", or just "Router") or integer
            (0 = router). An unknown name is rejected with the valid list and a
            "Did you mean …?" suggestion instead of being sent to PT.
        model: PT model string (e.g. "2911", "2960-24TT").
        x, y: canvas coordinates.
        skip_boot: press *Skip boot* right after adding, so the device is usable
            without waiting out the IOS boot.

    Returns ``{"name": ..., "ready": true|false}`` — ``ready`` is false while the
    device still boots, which is when the next call should use ``ready_timeout``.
    """
    try:
        resolved = resolve_device_type(type)
    except ValueError as exc:
        return _err(str(exc))
    return _tool(
        "add_device",
        _add_device,
        name,
        resolved,
        model,
        x,
        y,
        skip_boot,
    )


def _add_device(
    name: str,
    type_id: int,
    model: str,
    x: float,
    y: float,
    skip_boot: bool,
) -> dict[str, Any]:
    """``add_device`` (+ optional ``skip_boot``) as one tool result."""
    added = call_pt("add_device", {"name": name, "type": type_id, "model": model, "x": x, "y": y})
    payload = dict(added) if isinstance(added, dict) else {"name": name}
    if skip_boot:
        actual = str(payload.get("name") or name)
        call_pt("skip_boot", {"device": actual})
        payload["skipped_boot"] = True
    return payload


@server.tool()
def remove_device(name: str) -> str:
    """Remove a device (by name) from the active workspace."""
    return run_op("remove_device", {"name": name})


@server.tool()
def link(
    a: str,
    a_port: str,
    b: str,
    b_port: str,
    cable: str = "eCopperGigabitEthernet",
) -> str:
    """Create a physical link between two device ports.

    Args:
        a, a_port: first device name and port (e.g. "R1", "GigabitEthernet0/0").
        b, b_port: second device name and port.
        cable: cable name or CONNECT_TYPES int. Accepted names (case-insensitive,
            ``e`` optional): "eCopperEthernet"/"eCopperFastEthernet"/
            "eCopperGigabitEthernet", "eFiberFastEthernet"/
            "eFiberGigabitEthernet", "eSerial", "eCopperCoaxial", "straight"
            (default), "crossover", "fiber", "serial", "console", "wireless",
            "coax", "auto". An unknown name is rejected with the valid list and a
            closest match instead of being passed through to PT.
    """
    try:
        resolved = resolve_cable(cable)
    except ValueError as exc:
        return _err(str(exc))
    args = {"a": a, "aPort": a_port, "b": b, "bPort": b_port, "cable": resolved}
    return run_op("link", args)


@server.tool()
def autolink(a: str, b: str) -> str:
    """Auto-connect two devices (PT picks the default cable and ports)."""
    return run_op("autolink", {"a": a, "b": b})


@server.tool()
def power(
    device: str,
    on: bool = True,
    ready: bool = False,
    ready_timeout: float = READY_TIMEOUT,
) -> str:
    """Turn a device's power on (on=True) or off (on=False).

    Args:
        device: device name.
        on: True powers on, False powers off.
        ready: after powering on, wait until the device prompt is usable.
        ready_timeout: seconds to wait for that prompt when ``ready`` is true.

    A power cycle is the escape hatch for a wedged console, but it discards
    everything that was not saved into the .pkt — see the Recovery section of
    docs/setup.md before using it.
    """
    result = run_op("power", {"device": device, "on": bool(on)})
    if not on or not ready:
        return result
    failure = _wait_or_error(device, ready_timeout)
    return failure or result


@server.tool()
def skip_boot(device: str, wait: bool = False, ready_timeout: float = READY_TIMEOUT) -> str:
    """Press *Skip boot* on a device so it reaches its console without the boot.

    Args:
        device: device name.
        wait: after skipping, wait until the device prompt is usable.
        ready_timeout: seconds to wait for that prompt when ``wait`` is true.

    Use it on a freshly added IOS device (or one stuck in the boot sequence);
    ``add_device(..., skip_boot=True)`` does the same in one call.
    """
    return _tool("skip_boot", _skip_boot, device, bool(wait), ready_timeout)

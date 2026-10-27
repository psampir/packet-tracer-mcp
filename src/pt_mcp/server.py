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

Console semantics live in the bridge, not here: it reads the prompt before and
after every line, decides whether ``enable`` / ``configure terminal`` / ``end``
belong in the sequence, and waits for each line to be absorbed. This module adds
the one thing the bridge cannot do — *real waiting*: :func:`wait_ready` polls
``get_state`` with :func:`time.sleep` until a freshly added or freshly powered
device has a usable prompt (PTM-01). See ``docs/cli-semantics.md``.

Every tool returns a **JSON string** — a result, or ``{"ok": false, "error"}``
so an LLM client always gets something it can act on instead of a traceback.

SDK note: ``mcp`` 2.x renamed ``FastMCP`` to ``MCPServer``
(``from mcp.server.mcpserver import MCPServer``); the import below falls back to
the v1 ``FastMCP`` name so either major works. Transport: stdio only —
``main()`` starts the server with ``server.run("stdio")`` and prints nothing, so
the stream stays clean for the JSON-RPC handshake.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

try:  # mcp >= 2 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _MCPServerType
except ImportError:  # pragma: no cover - only hit with mcp < 2
    from mcp.server.fastmcp import FastMCP as _MCPServerType

from . import __version__, config
from .commands import normalize_commands
from .protocol import PTBridgeError, _err, _ok, call_pt, not_set_up, run_op

_INSTRUCTIONS = f"""\
Packet Tracer bridge (pt-mcp {__version__}). Packet Tracer must be running with a
.pkt file open and the com.psampir.ptmcp Script Module started; every tool acts on
the ACTIVE file.

Typical flow:
  1. pt_status()            -> PT version + device count (also proves the bridge works)
  2. pt_list_devices()      -> names, types, models, link count
  3. pt_add_device(...)     -> name, type (eDeviceType name or int), model, x, y
  4. pt_link(a, a_port, b, b_port) / pt_autolink(a, b)
  5. pt_configure(...)      -> IOS CLI; mode=auto|raw|exec|config, dry_run=True to preview
  6. pt_show(...)           -> one show/exec command
  7. pt_power(...) / pt_save(...)

Commands are wrapped for the mode the device is actually in (enable / configure
terminal / end), and a PC's shell gets no wrappers at all. Every call waits for
the device's prompt first (ready_timeout, default 45 s).

All results are JSON strings; failures come back as {{"ok": false, "error": ...}}.
If a call times out, Packet Tracer is not answering: check that PT is running, a
.pkt is open, the Script Module is Started (Extensions -> Scripting ->
Configure PT Script Modules...) and, if the file watcher is silent, click
'Run File' to force a rescan. pt_raw(js) evaluates arbitrary JS in the PT engine
and is an escape hatch, not a normal step.
"""

server = _MCPServerType("packet-tracer-mcp", instructions=_INSTRUCTIONS)

#: How often :func:`wait_ready` asks the bridge for the device state, in seconds.
READY_POLL_INTERVAL = 0.25

#: Prompt modes that mean "the console is usable" (see docs/cli-semantics.md).
_READY_MODES = frozenset({"user", "enable", "config", "shell"})

#: Default readiness budget, in seconds.
READY_TIMEOUT = 45.0


def wait_ready(device: str, timeout: float = READY_TIMEOUT) -> dict[str, Any]:
    """Block until ``device`` shows a usable prompt, or raise :class:`PTBridgeError`.

    Polls ``get_state`` every :data:`READY_POLL_INTERVAL` seconds (real
    ``time.sleep``; the bridge must not busy-wait for a boot) until the device
    reports ``booting: false`` and a prompt mode of ``user``, ``enable``,
    ``config`` or ``shell``. A freshly added router sits in
    ``--- System Configuration Dialog ---`` for tens of seconds, and commands
    sent before that are eaten as wizard answers (PTM-01).
    """
    deadline = time.monotonic() + max(timeout, 0.0)
    state: dict[str, Any] = {}

    while True:
        state = call_pt("get_state", {"device": device})
        if not state.get("booting") and state.get("mode") in _READY_MODES:
            return state
        if time.monotonic() >= deadline:
            raise PTBridgeError(
                f"device '{device}' was not ready within {timeout:g}s "
                f"(prompt={state.get('prompt')!r}, mode={state.get('mode')!r}, "
                f"booting={state.get('booting')}).\n"
                "The device is probably still booting: retry with a longer "
                "ready_timeout, power-cycle it with pt_power(on=True, ready=True), "
                "or click 'Skip boot' in its config dialog."
            )
        time.sleep(READY_POLL_INTERVAL)


def _wait_or_error(device: str, timeout: float) -> str | None:
    """Run :func:`wait_ready`, returning the JSON error string on failure."""
    try:
        wait_ready(device, timeout)
    except PTBridgeError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001 - surface anything to the client
        return _err(f"Unexpected error waiting for device '{device}': {exc}")
    return None


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
def pt_show(device: str, command: str, ready_timeout: float = READY_TIMEOUT) -> str:
    """Run a single show/exec command on a device and return the output.

    Args:
        device: device name.
        command: one exec-mode command (e.g. "show ip interface brief").
        ready_timeout: seconds to wait for the device prompt before giving up.
    """
    failure = _wait_or_error(device, ready_timeout)
    if failure:
        return failure
    return run_op("exec", {"device": device, "command": command})


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

    Returns ``{"name": ..., "ready": true|false}`` — ``ready`` is false while the
    device still boots, which is when the next call should use ``ready_timeout``.
    """
    args: dict[str, Any] = {"name": name, "type": type, "model": model, "x": x, "y": y}
    return run_op("add_device", args)


@server.tool()
def pt_remove_device(name: str) -> str:
    """Remove a device (by name) from the active workspace."""
    return run_op("remove_device", {"name": name})


@server.tool()
def pt_link(
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
        cable: cable name or CONNECT_TYPES int. Friendly names are mapped:
            "eCopperGigabitEthernet"/"copper"/"ethernet" -> ETHERNET_STRAIGHT (8100),
            "crossover"/"cross" -> 8101, "fiber" -> 8103, "serial" -> 8106,
            "console" -> 8108, "wireless" -> 8109, "coaxial"/"coax" -> 8110,
            "auto" -> 8107.
    """
    args = {"a": a, "aPort": a_port, "b": b, "bPort": b_port, "cable": cable}
    return run_op("link", args)


@server.tool()
def pt_autolink(a: str, b: str) -> str:
    """Auto-connect two devices (PT picks the default cable and ports)."""
    return run_op("autolink", {"a": a, "b": b})


@server.tool()
def pt_power(
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
    """
    result = run_op("power", {"device": device, "on": bool(on)})
    if not on or not ready:
        return result
    failure = _wait_or_error(device, ready_timeout)
    return failure or result


# --------------------------------------------------------------------------
# Change / escape
# --------------------------------------------------------------------------
@server.tool()
def pt_configure(
    device: str,
    commands: str | list[str],
    mode: str = "auto",
    keep_session: bool = False,
    stop_on_error: bool = True,
    dry_run: bool = False,
    ready_timeout: float = READY_TIMEOUT,
) -> str:
    """Send IOS CLI commands to a device and return the terminal output/prompt.

    Args:
        device: device name.
        commands: newline-separated string, or a list of command strings. Blank
            lines and '!' comments are dropped; nothing else is rewritten here.
        mode: how to reach the right CLI mode — ``"auto"`` (default: inspect the
            prompt and do the right thing), ``"raw"`` (send exactly these lines),
            ``"exec"`` (exec commands only: enable if needed, never config) or
            ``"config"`` (enable + configure terminal, end unless keep_session).
            Devices without IOS (PC, server, printer, ...) always get raw.
        keep_session: do not append ``end`` — leave the device in config mode for
            the next call.
        stop_on_error: stop the block at the first rejected/timed-out line
            (default true).
        dry_run: return the lines that *would* be sent, without sending them.
        ready_timeout: seconds to wait for the device prompt before giving up.
    """
    try:
        lines = normalize_commands(commands)
    except ValueError as exc:
        return _err(str(exc))

    args: dict[str, Any] = {
        "device": device,
        "lines": lines,
        "mode": mode,
        "keep_session": bool(keep_session),
        "stop_on_error": bool(stop_on_error),
    }

    if dry_run:
        try:
            plan = call_pt("plan", args)
        except PTBridgeError as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything to the client
            return _err(f"Unexpected error calling PT bridge: {exc}")
        payload: dict[str, Any] = {"dry_run": True}
        if isinstance(plan, dict):
            payload.update(plan)
        else:  # pragma: no cover - the bridge always returns an object
            payload["lines"] = plan
        return _ok(payload)

    failure = _wait_or_error(device, ready_timeout)
    if failure:
        return failure
    return run_op("configure", args)


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
    # Only runs as `python -m pt_mcp.server`: the console script and the tests go
    # through the imported main(), which tests/test_protocol.py covers.
    main()  # pragma: no cover - true script entry point

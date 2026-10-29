"""IOS CLI tools: show, configure and save."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .. import config
from ..commands import normalize_commands
from ..protocol import PTBridgeError, _err, _ok, call_pt, run_op
from ..readiness import READY_TIMEOUT, _wait_or_error
from ..registry import server

# --------------------------------------------------------------------------
# IOS CLI: one show/exec command, a configure block, save
# --------------------------------------------------------------------------


@server.tool()
def show(device: str, command: str, ready_timeout: float = READY_TIMEOUT) -> str:
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


@server.tool()
def configure(
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
def save(path: str = "") -> str:
    """Save the active .pkt to a path (default: <base>/saved/<timestamp>.pkt)."""
    if not path:
        config.SAVED.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = str(config.SAVED / f"{stamp}.pkt")
    return run_op("save", {"path": path})

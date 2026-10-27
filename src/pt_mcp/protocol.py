"""The file-drop transport between this server and ``bridge/bridge.js``.

One call is one JSON file in, one JSON file out — no sockets, no shared memory:

1. the request ``{"id", "op", "args"}`` is written to ``<base>/jobs/<id>.json``
   **atomically** (write ``<id>.json.tmp``, then :func:`os.replace`), so the
   bridge's file watcher never sees a half-written file;
2. this module polls ``<base>/results/<id>.json`` until it appears or the
   deadline passes;
3. the response is read, deleted, and its ``result`` (or ``error``) returned.

The wire format and the op list are documented in ``docs/protocol.md``; the
bridge side of the contract is ``bridge/bridge.js``.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from typing import Any

from . import config

#: Shown when a job is not answered in time: almost always a PT-side problem.
_NOT_RESPONDING = (
    "Packet Tracer is not responding to the pt-mcp bridge.\n"
    "Checklist:\n"
    "  1. Cisco Packet Tracer 9.0.1 is running.\n"
    "  2. A .pkt file is open/active (the bridge configures the ACTIVE file).\n"
    "  3. The pt-mcp Script Module is started: Extensions -> Scripting ->\n"
    "     Configure PT Script Modules... -> select com.pawel.ptmcp -> Start.\n"
    "  4. If the file watcher is not firing, click 'Run File' in the\n"
    "     Scripting Interface to force a rescan.\n"
    "  5. The module's Security Privileges include Application + File Operations\n"
    "     + Get/Change Network Info."
)

#: Shown when the base directory is absent — the module never ran.
_NOT_SET_UP = (
    "The pt-mcp bridge was never set up: base dir {base} does not exist.\n"
    "Start the bridge once (a Script Module start or 'Run File' in PT creates\n"
    "it), or create it manually:\n"
    "    mkdir -p {base}/jobs {base}/results {base}/saved"
)


def not_set_up(base: object) -> str:
    """Return the "bridge was never set up" text for ``base``."""
    return _NOT_SET_UP.format(base=base)


class PTBridgeError(RuntimeError):
    """Raised when the bridge reports an error or does not answer in time."""


def call_pt(op: str, args: dict | None = None, timeout: float | None = None) -> Any:
    """Send one op to the Packet Tracer bridge and return its ``result``.

    The request is written atomically (tmp + :func:`os.replace`), the matching
    response file is polled for, deleted once read, and its parsed ``result``
    returned. Raises :class:`PTBridgeError` when the bridge reports an error or
    the deadline passes; a timed-out job file is removed so it cannot be picked
    up (and answered) later.
    """
    args = args or {}
    if timeout is None:
        timeout = config.TIMEOUT

    base = config.BASE
    if not base.exists():
        raise PTBridgeError(not_set_up(base))

    jobs = base / "jobs"
    results = base / "results"
    jobs.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)

    job_id = uuid.uuid4().hex[:12]
    request = {"id": job_id, "op": op, "args": args}

    tmp_path = jobs / f"{job_id}.json.tmp"
    job_path = jobs / f"{job_id}.json"
    tmp_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, job_path)

    response_path = results / f"{job_id}.json"
    deadline = time.monotonic() + timeout

    while True:
        if response_path.exists():
            try:
                raw = response_path.read_text(encoding="utf-8")
                data = json.loads(raw)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                # The bridge may still be writing — retry shortly.
                if time.monotonic() >= deadline:
                    break
                time.sleep(config.POLL_INTERVAL)
                continue

            with contextlib.suppress(OSError):
                response_path.unlink()

            if data.get("id") != job_id:
                # Stale or mismatched file; keep waiting for ours.
                if time.monotonic() >= deadline:
                    break
                time.sleep(config.POLL_INTERVAL)
                continue

            if data.get("ok"):
                return data.get("result")
            raise PTBridgeError(
                f"Packet Tracer bridge error for op '{op}': {data.get('error', 'unknown error')}"
            )

        if time.monotonic() >= deadline:
            break
        time.sleep(config.POLL_INTERVAL)

    # Timed out — drop our job so it is not processed later behind our back.
    with contextlib.suppress(OSError):
        job_path.unlink()
    raise PTBridgeError(f"Timed out after {timeout:.0f}s waiting for op '{op}'.\n{_NOT_RESPONDING}")


def _ok(result: Any) -> str:
    """Render a bridge result as the pretty JSON string the MCP tools return."""
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


def _err(message: str) -> str:
    """Render a failure as ``{"ok": false, "error": ...}`` instead of a traceback."""
    return json.dumps({"ok": False, "error": message}, indent=2, ensure_ascii=False)


def run_op(op: str, args: dict | None = None, timeout: float | None = None) -> str:
    """``call_pt`` with consistent JSON-string error handling for the MCP tools.

    Every failure an agent can act on (bridge error, timeout, unexpected
    exception, bad input) becomes a JSON object with ``ok: false`` and a
    human-readable ``error``, never a Python traceback.
    """
    try:
        return _ok(call_pt(op, args, timeout))
    except PTBridgeError as exc:
        return _err(str(exc))
    except Exception as exc:  # noqa: BLE001 - surface anything to the client
        return _err(f"Unexpected error calling PT bridge: {exc}")


__all__ = [
    "PTBridgeError",
    "call_pt",
    "not_set_up",
    "run_op",
]

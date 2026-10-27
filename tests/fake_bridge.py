#!/usr/bin/env python3
"""fake_bridge.py -- stand-in for the Packet Tracer Script Module bridge.

Lets you test the MCP server (``pt_mcp.server``) and the file-drop protocol
WITHOUT Packet Tracer. It polls ``<PT_MCP_HOME>/jobs`` and writes canned but
plausible responses to ``<PT_MCP_HOME>/results``, using exactly the same
JSON/file semantics as ``bridge/bridge.js`` (consume the job, delete it, write
one response per request id).

The test suite normally starts this for you (see ``tests/conftest.py``); to poke
at the protocol by hand:

    PT_MCP_HOME=/tmp/pt-fake python3 tests/fake_bridge.py
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("PT_MCP_HOME", "/home/pawel/.local/share/pt-mcp")).expanduser()
JOBS = HOME / "jobs"
RESULTS = HOME / "results"

POLL = 0.05

# Canned state -------------------------------------------------------------
_devices: dict[str, dict] = {}
_links = 0
_running = True

_DEVICE_TYPES = {0: "eRouter", 1: "eSwitch", 8: "ePc", 9: "eServer"}
_TYPE_IDS = {v: k for k, v in _DEVICE_TYPES.items()}


def _resolve_type(value):
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
            return int(value)
        return _TYPE_IDS.get(value, 0)
    return 0


def handle(op: str, args: dict):
    global _links

    if op == "ping":
        return {"version": "9.0.1-fake", "deviceCount": len(_devices)}

    if op == "list_devices":
        devices = [
            {
                "name": d["name"],
                "type": d["type"],
                "typeName": _DEVICE_TYPES.get(d["type"], "eUnknown"),
                "model": d["model"],
            }
            for d in _devices.values()
        ]
        return {"devices": devices, "links": _links}

    if op == "add_device":
        type_id = _resolve_type(args.get("type"))
        name = args.get("name") or f"Device{len(_devices)}"
        _devices[name] = {
            "name": name,
            "type": type_id,
            "model": args.get("model", ""),
        }
        return {"name": name}

    if op == "remove_device":
        name = args.get("name")
        removed = name in _devices
        _devices.pop(name, None)
        return {"removed": removed}

    if op == "link":
        _links += 1
        return {"linked": True}

    if op == "autolink":
        _links += 1
        return {"ok": True}

    if op == "configure":
        device = args.get("device", "?")
        lines = args.get("lines", [])
        body = "\n".join(lines)
        return {
            "output": f"[fake] {device} received:\n{body}\n",
            "prompt": f"{device}(config)#",
        }

    if op == "show":
        device = args.get("device", "?")
        command = args.get("command", "")
        return {"output": f"[fake] {command}\n{device}#", "prompt": f"{device}#"}

    if op == "power":
        return {"ok": True}

    if op == "skip_boot":
        return {"ok": True}

    if op == "save":
        return {"saved": True, "path": args.get("path")}

    if op == "eval":
        return {"value": f"[fake eval] {args.get('js', '')}"}

    raise ValueError(f"unknown op: {op}")


def write_result(job_id, ok, payload):
    obj = {"id": job_id, "ok": bool(ok)}
    if ok:
        obj["result"] = payload
    else:
        obj["error"] = str(payload)
    (RESULTS / f"{job_id}.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def process(name: str) -> None:
    job_path = JOBS / name
    job_id = name[:-5]  # strip .json
    try:
        req = json.loads(job_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        write_result(job_id, False, f"invalid job JSON: {exc}")
        job_path.unlink(missing_ok=True)
        return

    job_id = req.get("id", job_id)
    op = req.get("op", "")
    args = req.get("args", {}) or {}
    try:
        result = handle(op, args)
        write_result(job_id, True, result)
    except Exception as exc:  # noqa: BLE001
        write_result(job_id, False, str(exc))
    finally:
        job_path.unlink(missing_ok=True)


def scan() -> None:
    if not JOBS.is_dir():
        return
    for name in sorted(os.listdir(JOBS)):
        if not name.endswith(".json"):
            continue
        process(name)


def _stop(signum, frame):  # noqa: ARG001
    global _running
    _running = False


def main() -> None:
    JOBS.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    print(f"[fake_bridge] ready, home={HOME}", flush=True)
    while _running:
        scan()
        time.sleep(POLL)
    print("[fake_bridge] stopped", flush=True)


if __name__ == "__main__":
    sys.exit(main())

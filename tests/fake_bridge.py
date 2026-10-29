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

_DEVICE_TYPES = {
    0: "eRouter",
    1: "eSwitch",
    8: "ePc",
    9: "eServer",
    10: "ePrinter",
    16: "eMultiLayerSwitch",
    26: "eASA",
    39: "eWirelessLanController",
}
_TYPE_IDS = {v: k for k, v in _DEVICE_TYPES.items()}

#: Device types that speak IOS inside Packet Tracer (the bridge's allowlist).
_IOS_TYPES = {0, 1, 16, 26, 39}

_EXEC_VERBS = {
    "show", "ping", "traceroute", "telnet", "ssh", "clear", "copy", "write",
    "reload", "dir", "more", "debug", "undebug", "clock", "exit", "enable",
    "disable", "configure", "terminal",
}

#: Canned answers for the snippets the server generates for its host/topology
#: tools. ``fake_bridge.py`` has no JavaScript engine, so it recognises the
#: ``/* packet-tracer-mcp: <tool> */`` marker at the top of each snippet and answers with
#: plausible data; the real bridge evaluates the script instead. Anything
#: without a marker (i.e. ``raw``) is echoed back as text.
_EVAL_CANNED = {
    "/* packet-tracer-mcp: get_config */": [
        {
            "name": "FastEthernet0",
            "ip": "192.168.10.10",
            "mask": "255.255.255.0",
            "dhcp": False,
            "ipv6": [],
            "up": True,
        }
    ],
    "/* packet-tracer-mcp: set_ip */": {
        "port": "FastEthernet0",
        "ip": "192.168.10.10",
        "mask": "255.255.255.0",
        "dhcp": False,
    },
    "/* packet-tracer-mcp: set_ipv6 */": {
        "port": "FastEthernet0",
        "address": "2001:db8::10",
        "prefix": 64,
        "gateway": "2001:db8::1",
        "ipv6": ["2001:db8::10/64"],
    },
    "/* packet-tracer-mcp: dns_record */": {
        "name": "www.g4.lab",
        "ip": "192.168.20.10",
        "zone": "g4.lab",
        "ns": "Server-DNS",
        "mailbox": "admin.g4.lab",
        "enabled": True,
        "soa": True,
        "ns_record": True,
        "ip_entry": True,
        "a_record": True,
        "entries": 1,
    },
    "/* packet-tracer-mcp: dns_list */": {"count": 1, "entries": ["www.g4.lab 192.168.20.10"]},
    "/* packet-tracer-mcp: http_page */": {
        "path": "index.html",
        "enabled": True,
        "page": "<h1>pt-mcp fake server</h1>",
    },
    "/* packet-tracer-mcp: list_links */": [
        {
            "a": "R1",
            "a_port": "GigabitEthernet0/0",
            "b": "SW1",
            "b_port": "GigabitEthernet0/1",
            "cable": "8100",
            "up": True,
        }
    ],
    "/* packet-tracer-mcp: list_ports */": [
        {
            "name": "FastEthernet0",
            "up": True,
            "protocol_up": True,
            "ip": "192.168.10.10",
            "mask": "255.255.255.0",
            "link": "SW1 FastEthernet0/1",
        }
    ],
    "/* packet-tracer-mcp: pc_command len */": 0,
    "/* packet-tracer-mcp: pc_command output */": (
        "Pinging 192.168.10.1 with 32 bytes of data:\n\n"
        "Reply from 192.168.10.1: bytes=32 time=1ms TTL=128\n"
        "Reply from 192.168.10.1: bytes=32 time=1ms TTL=128\n"
        "Reply from 192.168.10.1: bytes=32 time=2ms TTL=128\n"
        "Reply from 192.168.10.1: bytes=32 time=1ms TTL=128\n\n"
        "Ping statistics for 192.168.10.1:\n"
        "    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),\n"
    ),
}


def _eval_canned(js: str):
    """Answer a server-generated snippet, or echo it back (``raw``)."""
    for marker, payload in _EVAL_CANNED.items():
        if marker in js:
            return {"value": json.dumps(payload, ensure_ascii=False)}
    return {"value": f"[fake eval] {js}"}


def _resolve_type(value):
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
            return int(value)
        return _TYPE_IDS.get(value, 0)
    return 0


def _prompt_mode(prompt: str) -> str:
    """Classify a prompt the way the bridge does (see docs/cli-semantics.md)."""
    text = str(prompt or "").rstrip()
    if not text:
        return "unknown"
    if text.endswith("#"):
        return "config" if "(config" in text else "enable"
    if text.endswith(">"):
        return "shell" if len(text) > 1 and text[1] == ":" else "user"
    return "unknown"


def _device(name: str):
    device = _devices.get(name)
    if device is None:
        raise ValueError(f"device not found: {name}")
    return device


def _state(device: dict) -> dict:
    prompt = str(device["prompt"])
    return {
        "name": device["name"],
        "prompt": prompt,
        "mode": _prompt_mode(prompt),
        "booting": False,
        "hasIos": device["type"] in _IOS_TYPES,
        "type": device["type"],
        "model": device["model"],
    }


def _first_meaningful(lines: list[str]) -> str:
    for line in lines:
        text = str(line).strip()
        if text:
            return text
    return ""


def _is_exec_verb(line: str) -> bool:
    word = line.split()[0].lower() if line.split() else ""
    return word in _EXEC_VERBS


def _wrap_config(current_mode: str, lines: list[str], keep_session: bool) -> list[str]:
    out: list[str] = []
    if current_mode == "user":
        out.append("enable")
    if current_mode != "config":
        out.append("configure terminal")
    out.extend(lines)
    last = ""
    for line in reversed(lines):
        if str(line).strip():
            last = str(line).strip()
            break
    if not keep_session and last.lower() != "end":
        out.append("end")
    return out


def _plan(device: dict, args: dict) -> dict:
    """A deliberately small mirror of the bridge's mode rules."""
    plan = _plan_lines(device, args)
    state = _state(device)
    plan["prompt"] = state["prompt"]
    plan["booting"] = state["booting"]
    return plan


def _plan_lines(device: dict, args: dict) -> dict:
    lines = [str(line) for line in (args.get("lines") or [])]
    if not lines:
        raise ValueError("plan: no lines supplied")
    requested = str(args.get("mode") or "auto").lower()
    if requested not in {"auto", "raw", "exec", "config"}:
        raise ValueError(f"unknown mode '{args.get('mode')}'; valid: auto, raw, exec, config")
    keep = bool(args.get("keep_session"))
    state = _state(device)
    mode = state["mode"]

    if not state["hasIos"] or requested == "raw":
        return {"lines": lines, "mode": requested, "effective_mode": "raw"}
    if requested == "exec":
        prefix = ["enable"] if mode == "user" else []
        return {"lines": prefix + lines, "mode": requested, "effective_mode": "exec"}
    if requested == "config":
        return {
            "lines": _wrap_config(mode, lines, keep),
            "mode": requested,
            "effective_mode": "config",
        }
    # auto
    if mode == "config":
        return {
            "lines": _wrap_config("config", lines, keep),
            "mode": requested,
            "effective_mode": "config",
        }
    if mode in ("user", "enable"):
        prefix = ["enable"] if mode == "user" else []
        if _is_exec_verb(_first_meaningful(lines)):
            return {"lines": prefix + lines, "mode": requested, "effective_mode": "exec"}
        return {
            "lines": prefix + _wrap_config("enable", lines, keep),
            "mode": requested,
            "effective_mode": "config",
        }
    return {"lines": lines, "mode": requested, "effective_mode": "raw"}


def _apply_line(device: dict, line: str) -> None:
    """Track just enough prompt state for follow-up calls to look plausible."""
    text = line.lower()
    name = device["name"].split("(")[0]
    if text in ("enable", "en") or text == "end":
        device["prompt"] = f"{name}#"
    elif text in ("configure terminal", "conf t", "config t", "configure t"):
        device["prompt"] = f"{name}(config)#"
    elif text.startswith("interface "):
        device["prompt"] = f"{name}(config-if)#"
    elif text == "exit":
        device["prompt"] = f"{name}(config)#" if "(config-if" in device["prompt"] else f"{name}#"


def _run(device: dict, plan: dict) -> dict:
    requested = plan["mode"]
    effective = plan["effective_mode"]
    records = []
    for line in plan["lines"]:
        _apply_line(device, line)
        records.append(
            {
                "line": line,
                "prompt": device["prompt"],
                "output": f"[fake] {line}\n",
                "ok": True,
            }
        )
    return {
        "ok": True,
        "prompt": device["prompt"],
        "lines": records,
        "output": "".join(record["output"] for record in records),
        "stopped_on": None,
        "mode": requested,
        "effective_mode": effective,
    }


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
            # IOS devices start at their enable prompt; hosts at a shell.
            "prompt": f"{name}#" if type_id in _IOS_TYPES else "C:\\>",
        }
        return {"name": name, "ready": True}

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

    if op == "get_state":
        return _state(_device(args.get("device", "")))

    if op == "plan":
        return _plan(_device(args.get("device", "")), args)

    if op == "configure":
        device = _device(args.get("device", ""))
        return _run(device, _plan(device, args))

    if op in ("exec", "show"):
        device = _device(args.get("device", ""))
        command = str(args.get("command", ""))
        if not command.strip():
            raise ValueError(f"{op}: no command supplied")
        return _run(device, _plan(device, {"lines": [command], "mode": "exec"}))

    if op == "power":
        return {"ok": True}

    if op == "skip_boot":
        return {"ok": True}

    if op == "save":
        return {"saved": True, "path": args.get("path")}

    if op == "eval":
        return _eval_canned(str(args.get("js", "")))

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

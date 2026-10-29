#!/usr/bin/env python3
"""Live integration smoke test for packet-tracer-mcp against a running Packet Tracer.

The offline suite (``uv run pytest``) proves the transport and the tool logic.
This script proves the part that only Packet Tracer can answer: the real IPC
calls, the boot wizard, DHCP leases, DNS/HTTP services, routing and ping.

Requirements
------------
Packet Tracer 9.x running, a ``.pkt`` open, and the ``com.psampir.packet-tracer-mcp``
Script Module started (see ``docs/setup.md``). Everything is built in the
**active** file and removed again, whether the run passes or fails.

Usage
-----
    uv run python tools/live_smoke.py            # fast run (skip_boot routers)
    uv run python tools/live_smoke.py --slow     # + a full-boot router test
    uv run python tools/live_smoke.py --keep     # leave the lab in place
    uv run python tools/live_smoke.py --verbose  # print every device operation

The script never saves the active file and never touches devices it did not
create (except stale ``LSMOKE-*`` devices from a previous interrupted run).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pt_mcp import server

PREFIX = "LSMOKE-"
HOST = "192.168.100.10"
PEER = "192.168.100.11"


class SmokeFailure(AssertionError):
    """A live check failed."""


def result(raw: str) -> Any:
    """Parse a tool result and turn ``{"ok": false}`` into a failure."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - transport corruption
        raise SmokeFailure(f"tool returned non-JSON: {raw!r}") from exc
    if isinstance(data, dict) and data.get("ok") is False:
        raise SmokeFailure(str(data.get("error", data)))
    return data


def wait_for(check: Callable[[], bool], timeout: float = 25.0, interval: float = 0.5) -> bool:
    """Poll ``check`` until it is true or the budget runs out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval)
    return False


@dataclass
class Lab:
    """Devices created by this run, removed in reverse order afterwards."""

    created: list[str] = field(default_factory=list)
    verbose: bool = False

    def add(self, name: str, kind: str, model: str, *, skip_boot: bool = False) -> str:
        data = result(
            server.add_device(
                name=name,
                type=kind,
                model=model,
                x=100 + 80 * len(self.created),
                y=200,
                skip_boot=skip_boot,
            )
        )
        self.created.append(str(data["name"]))
        if self.verbose:
            print(f"    + {data['name']} ({kind} {model})")
        return str(data["name"])

    def link(self, a: str, a_port: str, b: str, b_port: str, cable: str = "straight") -> None:
        data = result(server.link(a=a, a_port=a_port, b=b, b_port=b_port, cable=cable))
        if data.get("linked") is not True:
            raise SmokeFailure(f"link {a}:{a_port} <-> {b}:{b_port} was not created")
        if self.verbose:
            print(f"    ~ {a}:{a_port} <-> {b}:{b_port} ({cable})")

    def cleanup(self) -> None:
        for name in reversed(self.created):
            try:
                result(server.remove_device(name=name))
                if self.verbose:
                    print(f"    - {name}")
            except Exception as exc:  # noqa: BLE001 - cleanup must not mask a failure
                print(f"    ! could not remove {name}: {exc}", file=sys.stderr)
        self.created.clear()


def clean_stale() -> None:
    """Remove devices left by an interrupted earlier run."""
    try:
        devices = result(server.list_devices()).get("devices", [])
    except Exception as exc:  # noqa: BLE001
        raise SmokeFailure(f"cannot list devices: {exc}") from exc
    for device in devices:
        name = str(device.get("name", ""))
        if name.startswith(PREFIX):
            with contextlib.suppress(Exception):
                result(server.remove_device(name=name))


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def check_status(_: Lab) -> None:
    status = result(server.status())
    assert status.get("version"), "status returned no version"
    assert "deviceCount" in status


def check_raw(_: Lab) -> None:
    assert result(server.raw(js='"ok"'))["value"] == "ok"
    assert result(server.raw(js="1+1"))["value"] == 2
    cut = result(server.raw(js='"x".repeat(100)', max_chars=20))
    assert cut.get("truncated") is True
    assert cut.get("chars") == 102
    bad = json.loads(server.raw(js='ipc.network().getDevice("x").getProcess("IP")'))
    assert bad["ok"] is False
    assert "unknown process 'IP'" in bad["error"]


def check_add_list_remove(lab: Lab) -> None:
    name = lab.add(PREFIX + "PC0", "ePc", "PC-PT")
    names = [d["name"] for d in result(server.list_devices())["devices"]]
    assert name in names, "added device is missing from list_devices"


def check_link_and_ports(lab: Lab) -> None:
    a = lab.add(PREFIX + "LNK-A", "ePc", "PC-PT")
    b = lab.add(PREFIX + "LNK-B", "ePc", "PC-PT")
    lab.link(a, "FastEthernet0", b, "FastEthernet0", "crossover")
    links = result(server.list_links())
    pair = {(link["a"], link["b"]) for link in links}
    present = (a, b) in pair or (b, a) in pair
    assert present, "list_links missed the new cable"
    ports = result(server.list_ports(device=a))
    assert any(b in str(port.get("link", "")) for port in ports), "list_ports has no peer"


def check_static_ip(lab: Lab) -> None:
    pc = lab.add(PREFIX + "IP", "ePc", "PC-PT")
    data = result(
        server.set_ip(
            device=pc, ip="192.168.90.10", mask="255.255.255.0", gateway="192.168.90.1"
        )
    )
    assert data["ip"] == "192.168.90.10", data
    assert data["dhcp"] is False, data
    config = result(server.get_config(device=pc, port="FastEthernet0"))["ports"][0]
    assert config["ip"] == "192.168.90.10", config
    # No link on this standalone PC, so `up` is legitimately false here.


def check_ipv6(lab: Lab) -> None:
    pc = lab.add(PREFIX + "V6", "ePc", "PC-PT")
    data = result(
        server.set_ipv6(
            device=pc, port="FastEthernet0", address="2001:db8:90::10", prefix=64
        )
    )
    assert data["ipv6"], "set_ipv6 returned no addresses"
    assert data["ipv6"][0].endswith("/64")
    assert "object" not in data["ipv6"][0]
    config = result(server.get_config(device=pc, port="FastEthernet0"))["ports"][0]
    assert config["ipv6"]
    assert config["ipv6"][0].endswith("/64")


def check_dns_http(lab: Lab) -> None:
    srv = lab.add(PREFIX + "SRV", "eServer", "Server-PT")
    result(
        server.set_ip(
            device=srv, ip="192.168.91.20", mask="255.255.255.0", gateway="192.168.91.1"
        )
    )
    record = result(
        server.dns_record(
            device=srv,
            name="www.smoke.lab",
            ip="192.168.91.20",
            zone="smoke.lab",
            ns_name="ns.smoke.lab",
            mailbox="hostmaster.smoke.lab",
        )
    )
    assert record["enabled"] is True
    assert record["a_record"] is True
    assert record["ip_entry"] is True
    entries = result(server.dns_list(device=srv))["entries"]
    assert entries == [{"name": "www.smoke.lab", "ip": "192.168.91.20"}], entries
    by_name = result(server.dns_list(device=srv, name="www.smoke.lab"))
    assert by_name["ip"] == "192.168.91.20", by_name

    page = result(server.http_page(device=srv, path="index.html", content="<h1>smoke</h1>"))
    assert page["enabled"] is True
    assert page["page"] == "<h1>smoke</h1>"
    kept = result(server.http_page(device=srv, path="index.html"))
    assert kept["page"] == "<h1>smoke</h1>", "content=None must keep the page"
    off = result(server.http_page(device=srv, path="index.html", enable=False))
    assert off["enabled"] is False

    removed = result(
        server.dns_record(
            device=srv, name="www.smoke.lab", ip="192.168.91.20", enable=False
        )
    )
    assert removed["ip_entry"] is False
    assert removed["entries"] == 0


def check_ping(lab: Lab) -> None:
    a = lab.add(PREFIX + "PING-A", "ePc", "PC-PT")
    b = lab.add(PREFIX + "PING-B", "ePc", "PC-PT")
    lab.link(a, "FastEthernet0", b, "FastEthernet0", "crossover")
    result(server.set_ip(device=a, ip=HOST, mask="255.255.255.0"))
    result(server.set_ip(device=b, ip=PEER, mask="255.255.255.0"))
    ok = result(server.ping(src=a, dst=PEER, wait=10))
    assert ok["received"] >= 3, f"ping failed: {ok['output']}"
    bad = result(server.ping(src=a, dst="192.168.100.99", wait=8))
    assert bad["received"] == 0
    assert bad["loss"] == 100


def check_router(lab: Lab, *, slow: bool) -> None:
    router = lab.add(PREFIX + "R1", "eRouter", "2911", skip_boot=not slow)
    configured = result(
        server.configure(
            device=router,
            commands=[
                "hostname " + PREFIX + "R1",
                "interface GigabitEthernet0/0",
                "ip address 192.168.92.1 255.255.255.0",
                "no shutdown",
            ],
        )
    )
    assert configured.get("ok") is True, configured
    shown = result(server.show(device=router, command="show running-config | include hostname"))
    assert PREFIX + "R1" in shown["output"]

    bad = json.loads(
        server.configure(
            device=router,
            commands=["interface GigabitEthernet0/1", "this-command-is-bogus"],
            stop_on_error=True,
        )
    )
    assert bad["ok"] is False, bad
    assert bad["stopped_on"]["index"] == 3, bad
    recovered = result(server.show(device=router, command="show ip interface brief"))
    assert "GigabitEthernet0/0" in recovered["output"]

    kept = result(
        server.configure(
            device=router,
            commands=["interface GigabitEthernet0/1", "description LIVE-SMOKE"],
            keep_session=True,
        )
    )
    assert "(config" in kept["prompt"], kept
    kept_show = result(
        server.show(
            device=router, command="show running-config | include description LIVE-SMOKE"
        )
    )
    assert "description LIVE-SMOKE" in kept_show["output"]


def check_router_slow(lab: Lab) -> None:
    check_router(lab, slow=True)


def check_dhcp(lab: Lab) -> None:
    router = lab.add(PREFIX + "DHCP-R", "eRouter", "2911", skip_boot=True)
    pc = lab.add(PREFIX + "DHCP-PC", "ePc", "PC-PT")
    lab.link(pc, "FastEthernet0", router, "GigabitEthernet0/0")
    result(
        server.configure(
            device=router,
            commands=[
                "ip dhcp pool SMOKE",
                "network 192.168.93.0 255.255.255.0",
                "default-router 192.168.93.1",
                "exit",
                "interface GigabitEthernet0/0",
                "ip address 192.168.93.1 255.255.255.0",
                "no shutdown",
            ],
        )
    )
    result(server.set_ip(device=pc, dhcp=True))

    def leased() -> bool:
        try:
            ports = result(server.get_config(device=pc, port="FastEthernet0"))["ports"]
            return bool(ports) and str(ports[0]["ip"]).startswith("192.168.93.")
        except Exception:  # noqa: BLE001 - a lease may still be pending
            return False

    assert wait_for(leased), "DHCP did not hand out a lease within 25 s"
    ok = result(server.ping(src=pc, dst="192.168.93.1", wait=8))
    assert ok["received"] >= 3, f"ping to the DHCP router failed: {ok['output']}"


def check_pc_command(lab: Lab) -> None:
    pc = lab.add(PREFIX + "CMD", "ePc", "PC-PT")
    result(server.set_ip(device=pc, ip="192.168.94.10", mask="255.255.255.0"))
    out = result(server.pc_command(device=pc, command="ipconfig", wait=3))
    assert "192.168.94.10" in out["output"], out


def check_power(lab: Lab) -> None:
    pc = lab.add(PREFIX + "PWR", "ePc", "PC-PT")
    result(server.power(device=pc, on=False))
    result(server.power(device=pc, on=True, ready=True))
    assert result(server.status())["version"]


def check_skip_boot(lab: Lab) -> None:
    router = lab.add(PREFIX + "SKIP", "eRouter", "2911")
    result(server.skip_boot(device=router))
    shown = result(server.show(device=router, command="show version | include IOS"))
    assert "IOS" in shown["output"], shown


def check_errors(lab: Lab) -> None:
    bad_type = json.loads(server.add_device(name=PREFIX + "BAD", type="banana", model="2911"))
    assert bad_type["ok"] is False
    assert "unknown device type 'banana'" in bad_type["error"]

    a = lab.add(PREFIX + "ERR-A", "ePc", "PC-PT")
    b = lab.add(PREFIX + "ERR-B", "ePc", "PC-PT")
    bad_cable = json.loads(
        server.link(
            a=a, a_port="FastEthernet0", b=b, b_port="FastEthernet0", cable="banana"
        )
    )
    assert bad_cable["ok"] is False
    assert "unknown cable 'banana'" in bad_cable["error"]


def check_save(_: Lab) -> None:
    path = os.path.join(tempfile.gettempdir(), "packet-tracer-mcp-live-smoke.pkt")
    try:
        saved = result(server.save(path=path))
        assert saved.get("saved") is True
        assert os.path.exists(path), "save wrote no file"
    finally:
        if os.path.exists(path):
            os.remove(path)


CHECKS: list[tuple[str, Callable[[Lab], None]]] = [
    ("status", check_status),
    ("raw", check_raw),
    ("add/list/remove", check_add_list_remove),
    ("link/ports", check_link_and_ports),
    ("static IPv4", check_static_ip),
    ("IPv6", check_ipv6),
    ("DNS/HTTP", check_dns_http),
    ("ping", check_ping),
    ("pc command", check_pc_command),
    ("power", check_power),
    ("skip_boot", check_skip_boot),
    ("DHCP", check_dhcp),
    ("router CLI", lambda lab: check_router(lab, slow=False)),
    ("errors", check_errors),
    ("save", check_save),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--slow", action="store_true", help="also run a full-boot router test")
    parser.add_argument("--keep", action="store_true", help="leave the created devices in place")
    parser.add_argument("--verbose", action="store_true", help="print every device operation")
    args = parser.parse_args(argv)

    lab = Lab(verbose=args.verbose)
    checks = list(CHECKS)
    if args.slow:
        checks.insert(-2, ("router CLI (full boot)", check_router_slow))

    try:
        status = result(server.status())
    except Exception as exc:  # noqa: BLE001
        print(f"Packet Tracer is not answering: {exc}", file=sys.stderr)
        print(
            "Start PT, open a .pkt and start the com.psampir.packet-tracer-mcp module.",
            file=sys.stderr,
        )
        return 2

    print(
        f"packet-tracer-mcp live smoke — "
        f"PT {status.get('version')} / {status.get('deviceCount')} devices"
    )
    clean_stale()

    failures = 0
    try:
        for name, check in checks:
            try:
                check(lab)
                print(f"  PASS  {name}")
            except Exception as exc:  # noqa: BLE001 - report each check independently
                failures += 1
                print(f"  FAIL  {name}: {exc}")
    finally:
        if args.keep:
            print(f"  kept {len(lab.created)} devices: {', '.join(lab.created)}")
        else:
            lab.cleanup()

    print(f"\n{len(checks) - failures}/{len(checks)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

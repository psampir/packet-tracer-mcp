"""Tests for the host, topology and raw-hardening tools (PTM-03/04/08/09/10/12/15).

None of these need Packet Tracer: every one of them is a generated ES5 snippet
sent through the bridge's existing ``eval`` op, so the tests script ``call_pt``,
inspect the generated JS, and answer with the JSON the bridge would produce.
A handful of end-to-end cases go through ``tests/fake_bridge.py`` as well.
"""

from __future__ import annotations

import json

import pytest

from pt_mcp import protocol, server
from pt_mcp.protocol import PTBridgeError


class FakeClock:
    """Deterministic stand-in for :mod:`time` inside ``pt_mcp.server``."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class Raw:
    """An eval value that must reach the server without a JSON round trip."""

    def __init__(self, value: object) -> None:
        self.value = value


class ScriptedBridge:
    """``call_pt`` stand-in: scripted answers, and every generated JS kept."""

    def __init__(self) -> None:
        self.ops: list[tuple[str, dict]] = []
        self.eval_answers: list[object] = []
        self.answers: dict[str, object] = {}
        self.fail: dict[str, Exception] = {}
        self.raw_eval: dict[str, object] | None = None

    @property
    def js(self) -> list[str]:
        return [str(args.get("js")) for op, args in self.ops if op == "eval"]

    def last_js(self) -> str:
        return self.js[-1]

    def ops_of(self, name: str) -> list[dict]:
        return [args for op, args in self.ops if op == name]

    def __call__(self, op: str, args: dict | None = None, timeout: float | None = None):
        given = dict(args or {})
        self.ops.append((op, given))
        if op in self.fail:
            raise self.fail[op]
        if op == "eval":
            if self.raw_eval is not None:
                return self.raw_eval
            if not self.eval_answers:
                raise AssertionError(f"unexpected eval call: {given.get('js')}")
            answer = self.eval_answers.pop(0)
            if isinstance(answer, Raw):
                return {"value": answer.value}
            return {"value": json.dumps(answer, ensure_ascii=False)}
        if op == "get_state":
            return self.answers.get(
                "get_state", {"prompt": "C:\\>", "mode": "shell", "booting": False}
            )
        return self.answers.get(op, {"ok": True})


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> ScriptedBridge:
    """A scripted bridge for the tool body under test."""
    scripted = ScriptedBridge()
    monkeypatch.setattr(server, "call_pt", scripted)
    # run_op()-based tools go through the transport module's own reference.
    monkeypatch.setattr(protocol, "call_pt", scripted)
    return scripted


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Replace the module clock so ``wait`` and readiness never really sleep."""
    fake_clock = FakeClock()
    monkeypatch.setattr(server, "time", fake_clock)
    return fake_clock


PING_OK = (
    "Pinging 192.168.10.1 with 32 bytes of data:\n"
    "\n"
    "Reply from 192.168.10.1: bytes=32 time=1ms TTL=128\n"
    "Reply from 192.168.10.1: bytes=32 time=1ms TTL=128\n"
    "Reply from 192.168.10.1: bytes=32 time=2ms TTL=128\n"
    "Reply from 192.168.10.1: bytes=32 time=1ms TTL=128\n"
    "\n"
    "Ping statistics for 192.168.10.1:\n"
    "    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),\n"
)


# --------------------------------------------------------------------------
# Device types and cables: name validation with a suggestion (PTM-09/10)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("written", "value"),
    [
        ("eRouter", 0),
        ("Router", 0),
        ("router", 0),
        ("eSWITCH", 1),
        ("Pc", 8),
        ("MultiLayerSwitch", 16),
        ("8", 8),
        (9, 9),
    ],
)
def test_resolve_device_type_accepts_names_with_or_without_e(written, value) -> None:
    assert server.resolve_device_type(written) == value


def test_unknown_device_type_lists_every_valid_name() -> None:
    with pytest.raises(ValueError, match="unknown device type 'eRoutr'") as excinfo:
        server.resolve_device_type("eRoutr")

    message = str(excinfo.value)
    assert "unknown device type 'eRoutr'" in message
    assert "valid: eRouter, eSwitch, eCloud, eBridge" in message
    assert "eCluster" in message
    assert "Did you mean eRouter?" in message


def test_device_type_suggestion_ignores_the_e_prefix() -> None:
    with pytest.raises(ValueError, match="Did you mean eSwitch"):
        server.resolve_device_type("Swtich")


def test_bool_is_not_a_device_type() -> None:
    with pytest.raises(ValueError, match="unknown device type 'True'"):
        server.resolve_device_type(True)


def test_bool_is_not_a_cable() -> None:
    with pytest.raises(ValueError, match="unknown cable 'True'"):
        server.resolve_cable(True)


def test_pt_add_device_rejects_an_unknown_type_without_calling_pt(fake, jload) -> None:
    result = jload(server.add_device("R9", "Routerr", "2911"))

    assert result["ok"] is False
    assert "unknown device type 'Routerr'" in result["error"]
    assert "Did you mean eRouter?" in result["error"]
    assert fake.ops == [], "nothing is sent to PT for an invalid type"


def test_pt_add_device_accepts_a_type_without_the_e_prefix(fake, jload) -> None:
    fake.answers["add_device"] = {"name": "R1", "ready": False}

    result = jload(server.add_device("R1", "Router", "2911"))

    assert result["name"] == "R1"
    assert fake.ops_of("add_device")[0]["type"] == 0


def test_pt_add_device_can_press_skip_boot(fake, jload) -> None:
    """PTM-08: one call adds the device *and* skips its boot."""
    fake.answers["add_device"] = {"name": "R2", "ready": False}
    fake.answers["skip_boot"] = {"ok": True}

    result = jload(server.add_device("R2", "eRouter", "2911", skip_boot=True))

    assert result["skipped_boot"] is True
    assert fake.ops_of("skip_boot") == [{"device": "R2"}]


def test_pt_add_device_surfaces_a_bridge_error(fake, jload) -> None:
    fake.fail["add_device"] = PTBridgeError("device name already used")

    result = jload(server.add_device("R1", "eRouter", "2911"))

    assert result["ok"] is False
    assert "device name already used" in result["error"]


@pytest.mark.parametrize(
    ("written", "wire"),
    [
        ("eCopperEthernet", 8100),
        ("eCopperFastEthernet", 8100),
        ("eCopperGigabitEthernet", 8100),
        ("eFiberFastEthernet", 8103),
        ("eFiberGigabitEthernet", 8103),
        ("eSerial", 8106),
        ("eCopperCoaxial", 8110),
        ("straight", 8100),
        ("crossover", 8101),
        ("fiber", 8103),
        ("serial", 8106),
        ("console", 8108),
        ("wireless", 8109),
        ("coax", 8110),
        ("auto", 8107),
        ("FIBER", 8103),
        ("8110", 8110),
        (8101, 8101),
    ],
)
def test_resolve_cable_accepts_every_documented_name(written, wire) -> None:
    assert server.resolve_cable(written) == wire


def test_unknown_cable_lists_the_valid_names_and_a_suggestion() -> None:
    with pytest.raises(ValueError, match="unknown cable 'wireles'") as excinfo:
        server.resolve_cable("wireles")

    message = str(excinfo.value)
    assert "unknown cable 'wireles'" in message
    assert "valid: eCopperEthernet, eCopperFastEthernet, eCopperGigabitEthernet" in message
    assert "auto" in message
    assert "Did you mean wireless?" in message


def test_pt_link_rejects_an_unknown_cable_without_calling_pt(fake, jload) -> None:
    result = jload(server.link("R1", "GigabitEthernet0/0", "SW1", "GigabitEthernet0/1", "wifi"))

    assert result["ok"] is False
    assert "unknown cable 'wifi'" in result["error"]
    assert fake.ops == []


def test_pt_link_maps_a_friendly_cable_name(fake, jload) -> None:
    result = jload(
        server.link("R1", "GigabitEthernet0/0", "SW1", "GigabitEthernet0/1", "crossover")
    )

    assert result["ok"] is True
    assert fake.ops_of("link")[0]["cable"] == 8101


# --------------------------------------------------------------------------
# The eval wrapper itself
# --------------------------------------------------------------------------
def test_eval_unwraps_the_double_encoded_value(fake) -> None:
    fake.eval_answers = [{"ports": []}]

    assert server._eval("script") == {"ports": []}


def test_eval_keeps_a_plain_string(fake) -> None:
    fake.eval_answers = ["hello"]

    assert server._eval("script") == "hello"


def test_eval_wraps_a_bridge_failure_with_context(fake) -> None:
    fake.fail["eval"] = PTBridgeError("device not found: ghost")

    with pytest.raises(PTBridgeError) as excinfo:
        server._eval("script", device="ghost", op="get_config")

    message = str(excinfo.value)
    assert message.startswith("PT API error: device not found: ghost")
    assert "op=get_config" in message
    assert "device=ghost" in message


def test_api_error_does_not_double_prefix_a_bridge_message() -> None:
    message = server._api_error("PT API error: boom", "R1", "set_ip")

    assert message == "PT API error: boom (op=set_ip, device=R1)"


def test_eval_rejects_an_answer_without_a_value(fake) -> None:
    fake.raw_eval = {"ports": []}

    with pytest.raises(PTBridgeError) as excinfo:
        server._eval("script", device="R1", op="raw")

    assert "no 'value'" in str(excinfo.value)
    assert "device=R1" in str(excinfo.value)


# --------------------------------------------------------------------------
# get_config
# --------------------------------------------------------------------------
CONFIG_PORT = {
    "name": "FastEthernet0",
    "ip": "192.168.10.10",
    "mask": "255.255.255.0",
    "dhcp": False,
    "ipv6": [],
    "up": True,
}


def test_pt_get_config_reads_every_port(fake, jload) -> None:
    fake.eval_answers = [[CONFIG_PORT, dict(CONFIG_PORT, name="FastEthernet1")]]

    result = jload(server.get_config("PC1"))

    assert [port["name"] for port in result["ports"]] == ["FastEthernet0", "FastEthernet1"]
    js = fake.last_js()
    assert "getPortCount" in js
    assert "getPortAt" in js
    assert "isDhcpClientOn" in js
    assert "getIpv6Addresses" in js
    assert "v6text" in js, "IPv6 entries are formatted as address/prefix"


def test_pt_get_config_reads_a_single_port(fake, jload) -> None:
    fake.eval_answers = [[CONFIG_PORT]]

    result = jload(server.get_config("PC1", "FastEthernet0"))

    assert result["ports"][0]["ip"] == "192.168.10.10"
    js = fake.last_js()
    assert 'getPort("FastEthernet0")' in js
    assert "getPortCount" not in js, "a named port is read directly"


def test_pt_get_config_rejects_a_non_list(fake, jload) -> None:
    fake.eval_answers = [{"oops": True}]

    result = jload(server.get_config("PC1"))

    assert result["ok"] is False
    assert "expected a port list" in result["error"]


# --------------------------------------------------------------------------
# set_ip
# --------------------------------------------------------------------------
SET_IP_READBACK = {
    "port": "FastEthernet0",
    "ip": "192.168.20.10",
    "mask": "255.255.255.0",
    "dhcp": False,
}


def test_pt_set_ip_writes_the_address_before_the_mask(fake, jload) -> None:
    fake.eval_answers = [SET_IP_READBACK]

    result = jload(
        server.set_ip("Server-DNS", ip="192.168.20.10", mask="255.255.255.0")
    )

    assert result["ip"] == "192.168.20.10"
    assert result["mask"] == "255.255.255.0"
    assert result["dhcp"] is False
    js = fake.last_js()
    assert 'setIpSubnetMask("192.168.20.10", "255.255.255.0")' in js, "ip first, mask second"
    assert "setDhcpClientFlag(false)" in js
    assert "setDefaultGateway" not in js
    assert "setDnsServerIp" not in js


def test_pt_set_ip_sets_the_gateway_and_dns(fake, jload) -> None:
    fake.eval_answers = [SET_IP_READBACK]

    jload(
        server.set_ip(
            "Server-DNS",
            port="GigabitEthernet0/0",
            ip="192.168.20.10",
            mask="255.255.255.0",
            gateway="192.168.20.1",
            dns="192.168.20.2",
        )
    )

    js = fake.last_js()
    assert 'getPort("GigabitEthernet0/0")' in js
    assert 'setDefaultGateway("192.168.20.1")' in js
    assert 'setDnsServerIp("192.168.20.2")' in js


def test_pt_set_ip_dhcp_starts_the_client(fake, jload) -> None:
    fake.eval_answers = [
        {"port": "FastEthernet0", "ip": "0.0.0.0", "mask": "0.0.0.0", "dhcp": True}
    ]

    result = jload(
        server.set_ip("PC-Users", dhcp=True, ip="10.0.0.9", mask="255.255.255.0")
    )

    assert result["dhcp"] is True
    js = fake.last_js()
    assert "setDhcpClientFlag(true)" in js
    assert 'getProcess("DhcpClient")' in js
    assert 'dhcpRun("FastEthernet0")' in js
    assert "setIpSubnetMask" not in js, "static fields are ignored when dhcp=True"


def test_pt_set_ip_rejects_half_an_address(fake, jload) -> None:
    result = jload(server.set_ip("PC1", ip="10.0.0.2"))

    assert result["ok"] is False
    assert "ip and mask must be given together" in result["error"]
    assert fake.ops == []


def test_pt_set_ip_rejects_a_non_object(fake, jload) -> None:
    fake.eval_answers = ["nope"]

    result = jload(server.set_ip("PC1", ip="10.0.0.2", mask="255.255.255.0"))

    assert result["ok"] is False
    assert "expected an address" in result["error"]


def test_pt_set_ip_reports_a_missing_dhcp_process(fake, jload) -> None:
    fake.fail["eval"] = PTBridgeError("no DhcpClient process on PC1")

    result = jload(server.set_ip("PC1", dhcp=True))

    assert result["ok"] is False
    assert "PT API error: no DhcpClient process on PC1" in result["error"]


# --------------------------------------------------------------------------
# set_ipv6
# --------------------------------------------------------------------------
def test_pt_set_ipv6_enables_and_adds_the_address(fake, jload) -> None:
    fake.eval_answers = [
        {
            "port": "FastEthernet0",
            "address": "2001:db8::10",
            "prefix": 64,
            "gateway": None,
            "ipv6": ["2001:db8::10/64"],
        }
    ]

    result = jload(server.set_ipv6("PC1", "FastEthernet0", "2001:db8::10"))

    assert result["ipv6"] == ["2001:db8::10/64"]
    js = fake.last_js()
    assert "setIpv6Enabled(true)" in js
    assert 'addIpv6Address("2001:db8::10", 64, 0, false)' in js
    assert "setv6DefaultGateway" not in js
    assert "v6text" in js, "entries are formatted as address/prefix, not [object Object]"
    assert "entry.address" in js


def test_pt_set_ipv6_sets_the_default_gateway(fake, jload) -> None:
    fake.eval_answers = [{"port": "FastEthernet0", "ipv6": []}]

    jload(server.set_ipv6("PC1", "FastEthernet0", "2001:db8::10", 96, "2001:db8::1"))

    js = fake.last_js()
    assert 'addIpv6Address("2001:db8::10", 96, 0, false)' in js
    assert 'setv6DefaultGateway("2001:db8::1")' in js


def test_pt_set_ipv6_rejects_a_non_object(fake, jload) -> None:
    fake.eval_answers = ["nope"]

    result = jload(server.set_ipv6("PC1", "FastEthernet0", "2001:db8::10"))

    assert result["ok"] is False
    assert "expected an address" in result["error"]


# --------------------------------------------------------------------------
# dns_record / dns_list (PTM-04)
# --------------------------------------------------------------------------
DNS_READBACK = {
    "name": "www.g4.lab",
    "ip": "192.168.20.10",
    "zone": "g4.lab",
    "ns": "Server-DNS",
    "enabled": True,
    "soa": True,
    "ns_record": True,
    "ip_entry": True,
    "a_record": True,
    "entries": 1,
}


def test_pt_dns_record_writes_both_a_records(fake, jload) -> None:
    """NXDOMAIN until *both* addIpAddress and addARecordToNameServerDb run."""
    fake.eval_answers = [DNS_READBACK]

    result = jload(server.dns_record("Server-DNS", "www.g4.lab", "192.168.20.10"))

    assert result["ip_entry"] is True
    assert result["a_record"] is True
    js = fake.last_js()
    assert 'addIpAddress("www.g4.lab", "192.168.20.10")' in js
    assert 'addARecordToNameServerDb("www.g4.lab", "192.168.20.10")' in js
    assert "dns.setEnable(true)" in js, "the DNS service is off by default"
    assert 'getProcess("DnsServer")' in js


def test_pt_dns_record_writes_the_zone_soa_and_ns_by_default(fake, jload) -> None:
    fake.eval_answers = [DNS_READBACK]

    jload(server.dns_record("Server-DNS", "www.g4.lab", "192.168.20.10"))

    js = fake.last_js()
    assert (
        'addSOAToNameServerDb("g4.lab", "Server-DNS", "admin.g4.lab", '
        "String(3600), String(3600), String(3600), String(3600))" in js
    )
    assert 'addNSRecordToNameServerDb("g4.lab", "Server-DNS")' in js


def test_pt_dns_record_zone_defaults_to_the_parent_domain(fake, jload) -> None:
    fake.eval_answers = [DNS_READBACK]

    jload(server.dns_record("DNS1", "lab.local", "10.0.0.5"))

    js = fake.last_js()
    assert 'addSOAToNameServerDb("lab.local", "DNS1", "admin.lab.local"' in js


def test_pt_dns_record_accepts_zone_ns_mailbox_and_timers(fake, jload) -> None:
    fake.eval_answers = [DNS_READBACK]

    jload(
        server.dns_record(
            "Server-DNS",
            "api.g4.lab",
            "192.168.20.11",
            zone="g4.lab",
            ns_name="ns.g4.lab",
            mailbox="hostmaster.g4.lab",
            min_ttl=60,
            refresh=120,
            retry=180,
            expiry=240,
        )
    )

    js = fake.last_js()
    assert (
        'addSOAToNameServerDb("g4.lab", "ns.g4.lab", "hostmaster.g4.lab", '
        "String(60), String(120), String(180), String(240))" in js
    )
    assert 'addNSRecordToNameServerDb("g4.lab", "ns.g4.lab")' in js


def test_pt_dns_record_can_skip_soa_and_ns(fake, jload) -> None:
    fake.eval_answers = [DNS_READBACK]

    jload(server.dns_record("Server-DNS", "www.g4.lab", "192.168.20.10", soa=False, ns=False))

    js = fake.last_js()
    assert "addSOAToNameServerDb" not in js
    assert "addNSRecordToNameServerDb" not in js


def test_pt_dns_record_removes_both_entries(fake, jload) -> None:
    fake.eval_answers = [dict(DNS_READBACK, enabled=False)]

    result = jload(server.dns_record("Server-DNS", "www.g4.lab", "192.168.20.10", enable=False))

    assert result["enabled"] is False
    js = fake.last_js()
    assert 'removeIpAddress("www.g4.lab")' in js
    assert 'removeARecordFromNameServerDb("www.g4.lab", "192.168.20.10")' in js
    assert "addIpAddress" not in js
    assert "addSOAToNameServerDb" not in js


def test_pt_dns_record_surfaces_a_process_error(fake, jload) -> None:
    fake.fail["eval"] = PTBridgeError("no DnsServer process on PC1")

    result = jload(server.dns_record("PC1", "www.g4.lab", "192.168.20.10"))

    assert result["ok"] is False
    assert "PT API error: no DnsServer process on PC1" in result["error"]


def test_pt_dns_record_rejects_a_non_object(fake, jload) -> None:
    fake.eval_answers = ["nope"]

    result = jload(server.dns_record("Server-DNS", "www.g4.lab", "192.168.20.10"))

    assert result["ok"] is False
    assert "expected a record" in result["error"]


def test_pt_dns_list_returns_every_entry(fake, jload) -> None:
    fake.eval_answers = [
        {
            "count": 2,
            "entries": [
                {"name": "www.g4.lab", "ip": "1.1.1.1"},
                {"name": "api.g4.lab", "ip": "2.2.2.2"},
            ],
        }
    ]

    result = jload(server.dns_list("Server-DNS"))

    assert result["count"] == 2
    assert result["entries"][1] == {"name": "api.g4.lab", "ip": "2.2.2.2"}
    js = fake.last_js()
    assert "getEntryCount" in js
    assert "getEntryAt" in js
    assert "entry.first" in js
    assert "entry.second" in js


def test_pt_dns_list_resolves_one_name(fake, jload) -> None:
    fake.eval_answers = [{"name": "www.g4.lab", "ip": "192.168.20.10"}]

    result = jload(server.dns_list("Server-DNS", "www.g4.lab"))

    assert result["ip"] == "192.168.20.10"
    assert 'getIpAddOfDomain("www.g4.lab")' in fake.last_js()


def test_pt_dns_list_rejects_a_non_object(fake, jload) -> None:
    fake.eval_answers = [["entry"]]

    result = jload(server.dns_list("Server-DNS"))

    assert result["ok"] is False
    assert "expected an entry" in result["error"]


# --------------------------------------------------------------------------
# http_page
# --------------------------------------------------------------------------
def test_pt_http_page_enables_and_writes_a_page(fake, jload) -> None:
    fake.eval_answers = [{"path": "index.html", "enabled": True, "page": "<h1>It works</h1>"}]

    result = jload(server.http_page("Server-DNS", "index.html", "<h1>It works</h1>"))

    assert result["page"] == "<h1>It works</h1>"
    js = fake.last_js()
    assert 'getProcess("HttpServer")' in js
    assert "setEnable(true)" in js
    assert 'setPageContents("index.html", "<h1>It works</h1>")' in js
    assert 'getPage("index.html")' in js


def test_pt_http_page_keeps_the_page_when_no_content_is_given(fake, jload) -> None:
    fake.eval_answers = [{"path": "index.html", "enabled": False, "page": "old"}]

    jload(server.http_page("Server-DNS", enable=False))

    js = fake.last_js()
    assert "setEnable(false)" in js
    assert "setPageContents" not in js


def test_pt_http_page_disable_does_not_query_the_disabled_server(fake, jload) -> None:
    """isEnabled()/getPage() after setEnable(false) takes Packet Tracer down."""
    fake.eval_answers = [{"path": "index.html", "enabled": False, "page": ""}]

    result = jload(server.http_page("Server-DNS", enable=False))

    assert result["enabled"] is False
    js = fake.last_js()
    assert "if (false)" in js, "a disabled HttpServer must not be queried"


def test_pt_http_page_rejects_a_non_object(fake, jload) -> None:
    fake.eval_answers = [7]

    result = jload(server.http_page("Server-DNS"))

    assert result["ok"] is False
    assert "expected a page" in result["error"]


# --------------------------------------------------------------------------
# pc_command
# --------------------------------------------------------------------------
PC_BUFFER = "C:\\>ping 10.0.0.1\nReply from 10.0.0.1: bytes=32 time=1ms TTL=128\n"


def test_pt_pc_command_returns_the_whole_new_output(fake, clock, jload) -> None:
    before = len("C:\\>ping 10.0.0.1\n")
    fake.eval_answers = [before, PC_BUFFER]

    result = jload(server.pc_command("PC1", "ping 10.0.0.1", wait=3))

    assert result["before"] == before
    assert result["output"] == "Reply from 10.0.0.1: bytes=32 time=1ms TTL=128\n"
    assert clock.sleeps == [3], "it waits for the output to accumulate"
    assert fake.ops_of("exec")[0]["command"] == "ping 10.0.0.1"
    assert fake.ops[0][0] == "get_state", "the host prompt is awaited first"
    assert "String(line.getOutput()).length" in fake.js[0], "the length is read before the command"
    assert "getCommandLine" in fake.js[1]


def test_pt_pc_command_skips_the_sleep_when_wait_is_zero(fake, clock, jload) -> None:
    fake.eval_answers = [0, "out"]

    jload(server.pc_command("PC1", "ipconfig", wait=0))

    assert clock.sleeps == []


def test_pt_pc_command_tolerates_a_reset_buffer(fake, clock, jload) -> None:
    fake.eval_answers = [9999, "short"]

    result = jload(server.pc_command("PC1", "ipconfig"))

    assert result["output"] == "short"


def test_pt_pc_command_tolerates_a_non_numeric_length(fake, clock, jload) -> None:
    fake.eval_answers = ["n/a", "abc"]

    result = jload(server.pc_command("PC1", "ipconfig"))

    assert result["before"] == 0
    assert result["output"] == "abc"


def test_pt_pc_command_reports_a_missing_buffer(fake, clock, jload) -> None:
    fake.fail["eval"] = PTBridgeError("device PC1 has no console output buffer")

    result = jload(server.pc_command("PC1", "ipconfig"))

    assert result["ok"] is False
    assert "no console output buffer" in result["error"]


def test_pt_pc_command_waits_for_readiness(fake, clock, jload) -> None:
    fake.answers["get_state"] = {"prompt": "", "mode": "unknown", "booting": True}
    fake.eval_answers = [0, "x"]

    result = jload(server.pc_command("PC1", "ipconfig", ready_timeout=0.5))

    assert result["ok"] is False
    assert "was not ready within 0.5s" in result["error"]


# --------------------------------------------------------------------------
# ping
# --------------------------------------------------------------------------
def test_pt_ping_parses_the_statistics(fake, clock, jload) -> None:
    fake.eval_answers = [0, PING_OK]

    result = jload(server.ping("PC1", "192.168.10.1"))

    assert (result["sent"], result["received"], result["loss"]) == (4, 4, 0)
    assert (result["replies"], result["timed_out"], result["unreachable"]) == (4, 0, 0)
    assert result["count"] == 4, "count is advisory but reported back"
    assert result["dst"] == "192.168.10.1"
    assert result["output"] == PING_OK


def test_pt_ping_counts_timeouts(fake, clock, jload) -> None:
    text = (
        "Reply from 192.168.10.1: bytes=32 time=1ms TTL=128\n"
        "Request timed out.\n"
        "Request timed out.\n"
        "Reply from 192.168.10.1: bytes=32 time=4ms TTL=128\n"
        "    Packets: Sent = 4, Received = 2, Lost = 2 (50% loss),\n"
    )
    fake.eval_answers = [0, text]

    result = jload(server.ping("PC1", "192.168.10.1"))

    assert (result["received"], result["loss"]) == (2, 50)
    assert (result["replies"], result["timed_out"]) == (2, 2)


def test_pt_ping_counts_an_unreachable_destination(fake, clock, jload) -> None:
    text = "Reply from 10.0.0.2: Destination host unreachable.\n" * 4
    fake.eval_answers = [0, text]

    result = jload(server.ping("PC1", "10.9.9.9"))

    assert result["replies"] == 0, "unreachable is not a reply"
    assert result["unreachable"] == 4
    assert (result["sent"], result["received"], result["loss"]) == (4, 0, 100)


def test_pt_ping_falls_back_when_statistics_are_missing(fake, clock, jload) -> None:
    fake.eval_answers = [0, "Reply from 10.0.0.1: bytes=32 time=1ms TTL=128\n"]

    result = jload(server.ping("PC1", "10.0.0.1", wait=5))

    assert (result["sent"], result["received"], result["loss"]) == (1, 1, 0)
    assert clock.sleeps == [5]


def test_pt_ping_reports_an_empty_output(fake, clock, jload) -> None:
    fake.eval_answers = [0, ""]

    result = jload(server.ping("PC1", "10.0.0.1"))

    assert (result["sent"], result["received"], result["loss"]) == (0, 0, 0)


# --------------------------------------------------------------------------
# skip_boot
# --------------------------------------------------------------------------
def test_pt_skip_boot_presses_the_button(fake, jload) -> None:
    fake.answers["skip_boot"] = {"ok": True}

    result = jload(server.skip_boot("R1"))

    assert result["ok"] is True
    assert fake.ops == [("skip_boot", {"device": "R1"})]


def test_pt_skip_boot_can_wait_for_the_prompt(fake, jload) -> None:
    fake.answers["skip_boot"] = {"ok": True}

    result = jload(server.skip_boot("R1", wait=True))

    assert result["ready"] is True
    assert [op for op, _ in fake.ops] == ["skip_boot", "get_state"]


def test_pt_skip_boot_surfaces_a_bridge_error(fake, jload) -> None:
    fake.fail["skip_boot"] = PTBridgeError("device not found: R9")

    result = jload(server.skip_boot("R9"))

    assert result["ok"] is False
    assert "device not found: R9" in result["error"]


# --------------------------------------------------------------------------
# list_links / list_ports (PTM-12)
# --------------------------------------------------------------------------
LINK = {
    "a": "R1",
    "a_port": "GigabitEthernet0/0",
    "b": "SW1",
    "b_port": "GigabitEthernet0/1",
    "cable": "8100",
    "up": True,
}


def test_pt_list_links_returns_the_topology(fake, jload) -> None:
    fake.eval_answers = [[LINK]]

    result = jload(server.list_links())

    assert result == [LINK]
    js = fake.last_js()
    assert "getPort1" in js
    assert "getPort2" in js
    assert "getConnectionType" in js
    assert "isPortUp" in js


def test_pt_list_links_dedupes_the_two_ends(fake, jload) -> None:
    reversed_link = {
        "a": "SW1",
        "a_port": "GigabitEthernet0/1",
        "b": "R1",
        "b_port": "GigabitEthernet0/0",
        "cable": "8100",
        "up": True,
    }
    fake.eval_answers = [[LINK, reversed_link, LINK]]

    result = jload(server.list_links())

    assert len(result) == 1, "one cable is one entry, whichever end was walked"
    assert result[0]["a"] == "R1"


def test_pt_list_links_keeps_four_distinct_cables(fake, jload) -> None:
    links = [
        dict(LINK),
        dict(LINK, a="SW1", a_port="FastEthernet0/1", b="PC-Users", b_port="FastEthernet0"),
        dict(LINK, a="SW1", a_port="FastEthernet0/2", b="Server-DNS", b_port="FastEthernet0"),
        dict(LINK, a="SW1", a_port="FastEthernet0/3", b="PC-Guest", b_port="FastEthernet0"),
    ]
    fake.eval_answers = [links + list(reversed(links))]

    result = jload(server.list_links())

    assert len(result) == 4


def test_pt_list_links_skips_junk_entries(fake, jload) -> None:
    fake.eval_answers = [[LINK, "not a link", {"a": "R1"}]]

    result = jload(server.list_links())

    assert result == [LINK], "a malformed entry is dropped, not passed on"


def test_pt_list_links_rejects_a_non_list(fake, jload) -> None:
    fake.eval_answers = [{"links": []}]

    result = jload(server.list_links())

    assert result["ok"] is False
    assert "expected a link list" in result["error"]


def test_pt_list_ports_reports_link_state(fake, jload) -> None:
    ports = [
        {
            "name": "FastEthernet0",
            "up": True,
            "protocol_up": True,
            "ip": "",
            "mask": "",
            "link": "SW1 FastEthernet0/1",
        }
    ]
    fake.eval_answers = [ports]

    result = jload(server.list_ports("PC1"))

    assert result == ports
    js = fake.last_js()
    assert "isProtocolUp" in js
    assert "getLink" in js
    assert 'getPort("PC1")' not in js


def test_pt_list_ports_rejects_a_non_list(fake, jload) -> None:
    fake.eval_answers = ["nope"]

    result = jload(server.list_ports("PC1"))

    assert result["ok"] is False
    assert "expected a port list" in result["error"]


# --------------------------------------------------------------------------
# raw hardening (PTM-15)
# --------------------------------------------------------------------------
def test_pt_raw_returns_a_real_json_value(fake) -> None:
    fake.eval_answers = [{"ok": True, "value": {"devices": [{"name": "R1"}]}}]

    payload = server.raw("ipc.network()")

    assert '"devices"' in payload
    assert '"value": {' in payload, "the object is not re-encoded as a string"
    assert json.loads(payload)["value"] == {"devices": [{"name": "R1"}]}


def test_pt_raw_wraps_the_script_for_error_reporting(fake) -> None:
    fake.eval_answers = [{"ok": True, "value": 2}]

    server.raw("1+1")

    js = fake.last_js()
    assert 'try{var __v=eval("1+1");' in js, "the source is a string literal"
    assert "JSON.stringify({ok:true,value:__v})" in js
    assert "line:(e&&e.lineNumber)||null" in js


def test_pt_raw_keeps_a_string_result_a_string(fake) -> None:
    """A string expression must not be re-evaluated as code (`"ok"` -> `ok`)."""
    fake.eval_answers = [{"ok": True, "value": "ok"}]

    server.raw('"ok"')

    assert 'eval("\\"ok\\"")' in fake.last_js()


def test_pt_raw_turns_a_thrown_error_into_a_message(fake, jload) -> None:
    fake.eval_answers = [{"ok": False, "error": "unknown process 'Bogus'", "line": 3}]

    result = jload(server.raw("device.getProcess('Bogus')"))

    assert result["ok"] is False
    assert result["error"].startswith("PT API error: unknown process 'Bogus'")
    assert "at line 3" in result["error"]
    assert "op=raw" in result["error"]


def test_pt_raw_rejects_a_too_short_process_name(fake, jload) -> None:
    """PTM-11: ``getProcess("IP")`` must not reach PT's C++ ``substr`` crash."""
    result = jload(server.raw("device.getProcess('IP')"))

    assert result["ok"] is False
    assert "unknown process 'IP'" in result["error"]
    assert "DnsServer" in result["error"], "the message lists valid process names"
    assert not fake.ops, "the invalid snippet never reached Packet Tracer"


def test_pt_raw_error_without_a_line_still_works(fake, jload) -> None:
    fake.eval_answers = [{"ok": False, "error": "boom", "line": None}]

    result = jload(server.raw("null.x"))

    assert result["ok"] is False
    assert "at line" not in result["error"]
    assert "PT API error: boom" in result["error"]


def test_pt_raw_truncates_a_large_result(fake, jload) -> None:
    fake.eval_answers = [{"ok": True, "value": "x" * 100}]

    result = jload(server.raw("'x'.repeat(100)", max_chars=20))

    assert result["truncated"] is True
    assert result["value"] == '"' + "x" * 19
    assert result["chars"] > 20


def test_pt_raw_keeps_a_small_result_whole(fake, jload) -> None:
    fake.eval_answers = [{"ok": True, "value": "x" * 10}]

    result = jload(server.raw("'x'.repeat(10)"))

    assert result == {"value": "x" * 10}


def test_pt_raw_can_switch_truncation_off(fake, jload) -> None:
    fake.eval_answers = [{"ok": True, "value": "x" * 50}]

    result = jload(server.raw("'x'.repeat(50)", max_chars=0))

    assert result["value"] == "x" * 50
    assert "truncated" not in result


def test_pt_raw_stringifies_an_unserialisable_value(fake, jload) -> None:
    fake.eval_answers = [Raw({"ok": True, "value": {"a", "b"}})]

    result = jload(server.raw("new Set(['a','b'])"))

    assert result["value"] in ("{'a', 'b'}", "{'b', 'a'}")


def test_pt_raw_passes_a_foreign_answer_through(fake, jload) -> None:
    """An older bridge (or the fake) may answer without the wrapper object."""
    fake.raw_eval = {"value": "plain"}

    result = jload(server.raw("1"))

    assert result == {"value": "plain"}


# --------------------------------------------------------------------------
# every eval-based tool reports a bridge failure as JSON, never a traceback
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: server.get_config("ghost"), id="get_config"),
        pytest.param(
            lambda: server.set_ip("ghost", ip="10.0.0.2", mask="255.255.255.0"),
            id="set_ip",
        ),
        pytest.param(
            lambda: server.set_ipv6("ghost", "FastEthernet0", "2001:db8::2"),
            id="set_ipv6",
        ),
        pytest.param(
            lambda: server.dns_record("ghost", "www.g4.lab", "10.0.0.2"),
            id="dns_record",
        ),
        pytest.param(lambda: server.dns_list("ghost"), id="dns_list"),
        pytest.param(lambda: server.http_page("ghost"), id="http_page"),
        pytest.param(lambda: server.list_links(), id="list_links"),
        pytest.param(lambda: server.list_ports("ghost"), id="list_ports"),
        pytest.param(lambda: server.raw("ipc.network()"), id="raw"),
    ],
)
def test_eval_tools_surface_bridge_errors_as_json(fake, jload, call) -> None:
    fake.fail["eval"] = PTBridgeError("device not found: ghost")

    result = jload(call())

    assert result["ok"] is False
    assert "PT API error: device not found: ghost" in result["error"]


def test_eval_tools_surface_unexpected_errors(fake, jload, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(server, "call_pt", boom)

    result = jload(server.get_config("PC1"))

    assert result["ok"] is False
    assert "disk on fire" in result["error"]


# --------------------------------------------------------------------------
# end-to-end through tests/fake_bridge.py (marked eval answers)
# --------------------------------------------------------------------------
def test_pt_get_config_end_to_end(jload) -> None:
    jload(server.add_device("PC-E2E", "ePc", "PC-PT"))

    config = jload(server.get_config("PC-E2E"))

    assert config["ports"][0]["name"] == "FastEthernet0"
    assert config["ports"][0]["ip"] == "192.168.10.10"


def test_pt_set_ip_end_to_end(jload) -> None:
    jload(server.add_device("PC-SETIP", "ePc", "PC-PT"))

    result = jload(server.set_ip("PC-SETIP", ip="192.168.10.10", mask="255.255.255.0"))

    assert result["ip"] == "192.168.10.10"
    assert result["dhcp"] is False


def test_pt_dns_record_end_to_end(jload) -> None:
    jload(server.add_device("Server-E2E", "eServer", "Server-PT"))

    record = jload(server.dns_record("Server-E2E", "www.g4.lab", "192.168.20.10"))

    assert record["ip_entry"] is True
    assert record["a_record"] is True


def test_pt_list_links_end_to_end(jload) -> None:
    links = jload(server.list_links())

    assert isinstance(links, list)
    assert links[0]["cable"] == "8100"


def test_pt_ping_end_to_end(jload) -> None:
    jload(server.add_device("PC-PING", "ePc", "PC-PT"))

    pinged = jload(server.ping("PC-PING", "192.168.10.1", wait=0))

    assert (pinged["sent"], pinged["received"], pinged["loss"]) == (4, 4, 0)


def test_pt_skip_boot_end_to_end(jload) -> None:
    result = jload(server.skip_boot("PC-PING"))

    assert result["ok"] is True


def test_pt_raw_end_to_end_echoes_unmarked_js(jload) -> None:
    raw = jload(server.raw("ipc.network().getDeviceCount()"))

    assert "[fake eval]" in raw["value"], "unmarked JS is echoed by the fake bridge"

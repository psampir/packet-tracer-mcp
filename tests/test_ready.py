"""Tests for readiness handling (:func:`pt_mcp.server.wait_ready`, PTM-01).

The rule these tests pin down: a freshly added or freshly powered device is not
usable until its console shows a real prompt. Sending ``hostname X`` into
``--- System Configuration Dialog ---`` types it as a wizard answer, and sending
``configure terminal`` during boot produces ``% Invalid input``.

The bridge answers ``get_state``; this module is the side that *waits* (real
``time.sleep``), so the clock is replaced with a deterministic fake here.
"""

from __future__ import annotations

import pytest
from seams import install_bridge, install_clock

from pt_mcp import server
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


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    install_clock(monkeypatch, fake)
    return fake


class FakeBridge:
    """Scripted answers for ``get_state``; records every op it is asked for."""

    def __init__(self, states: list[dict]) -> None:
        self.states = list(states)
        self.ops: list[tuple[str, dict]] = []
        self.execs: list[str] = []
        self.presses: list[list[str]] = []

    def __call__(self, op: str, args: dict | None = None, timeout: float | None = None):
        self.ops.append((op, args or {}))
        if op == "exec":
            self.execs.append(str((args or {}).get("command")))
            return {"ok": True, "prompt": "R1>", "lines": []}
        if op == "configure":
            self.presses.append(list((args or {}).get("lines") or []))
            return {"ok": True, "prompt": "Router>", "lines": []}
        if op != "get_state":
            raise AssertionError(f"unexpected op in readiness test: {op}")
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0]


def booting(prompt: str = "") -> dict:
    return {"prompt": prompt, "mode": "unknown", "booting": True}


def ready(prompt: str = "R1#", mode: str = "enable") -> dict:
    return {"prompt": prompt, "mode": mode, "booting": False}


def setup_dialog() -> dict:
    """The IOS initial-configuration dialog a fresh router can park at."""
    return {
        "prompt": "Would you like to enter the initial configuration dialog? [yes/no]: ",
        "mode": "unknown",
        "booting": False,
    }


# --------------------------------------------------------------------------
# wait_ready
# --------------------------------------------------------------------------
def test_wait_ready_returns_once_the_prompt_is_usable(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    fake = FakeBridge(
        [booting(), booting(), booting("--- System Configuration Dialog ---"), ready()]
    )
    install_bridge(monkeypatch, fake)

    state = server.wait_ready("R1", timeout=10.0)

    assert state["prompt"] == "R1#"
    assert len(fake.ops) == 4, "three boot states, then the ready one"
    assert clock.sleeps == [server.READY_POLL_INTERVAL] * 3, "one poll per boot state"


def test_wait_ready_is_immediate_when_the_device_is_ready(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    fake = FakeBridge([ready()])
    install_bridge(monkeypatch, fake)

    server.wait_ready("R1", timeout=10.0)

    assert len(fake.ops) == 1
    assert clock.sleeps == [], "a ready device costs exactly one round trip"


@pytest.mark.parametrize(
    ("prompt", "mode"),
    [("Switch>", "user"), ("Switch#", "enable"), ("S1(config)#", "config"), ("C:\\>", "shell")],
)
def test_wait_ready_accepts_every_usable_prompt_mode(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock, prompt: str, mode: str
) -> None:
    install_bridge(monkeypatch, FakeBridge([ready(prompt, mode)]))

    assert server.wait_ready("X", timeout=1.0)["mode"] == mode


def test_wait_ready_times_out_with_device_prompt_and_budget(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    install_bridge(monkeypatch, FakeBridge([booting()]))

    with pytest.raises(PTBridgeError) as excinfo:
        server.wait_ready("R1", timeout=1.0)

    message = str(excinfo.value)
    assert "device 'R1' was not ready within 1s" in message
    assert "prompt=''" in message
    assert "still booting" in message, "the error says what to do next"
    assert clock.sleeps, "it polled instead of giving up immediately"


def test_wait_ready_times_out_on_an_unknown_prompt_even_when_not_booting(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    state = {"prompt": "%%%%", "mode": "unknown", "booting": False}
    install_bridge(monkeypatch, FakeBridge([state]))

    with pytest.raises(PTBridgeError, match="mode='unknown'"):
        server.wait_ready("R1", timeout=0.5)


def test_wait_ready_answers_the_initial_configuration_dialog(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """A fresh IOS router waits at ``[yes/no]``; one ``no`` reaches the prompt."""
    fake = FakeBridge([setup_dialog(), ready("R1>", "user")])
    install_bridge(monkeypatch, fake)

    state = server.wait_ready("R1", timeout=10.0)

    assert state["mode"] == "user"
    assert fake.execs == ["no"], "exactly one answer, not a loop of 'no'"
    assert [op for op, _ in fake.ops] == ["get_state", "exec", "get_state"]


def test_wait_ready_presses_return_after_the_setup_dialog(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """After ``no`` IOS shows no prompt until RETURN; one empty line is sent."""
    fake = FakeBridge(
        [
            setup_dialog(),
            {"prompt": "", "mode": "unknown", "booting": False},
            ready("Router>", "user"),
        ]
    )
    install_bridge(monkeypatch, fake)

    state = server.wait_ready("R1", timeout=10.0)

    assert state["prompt"] == "Router>"
    assert fake.execs == ["no"]
    assert fake.presses == [[""]], "exactly one RETURN, as an empty raw line"


def test_wait_ready_reports_a_stuck_setup_dialog(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """If ``no`` does not take, the error names the dialog and the way out."""
    install_bridge(monkeypatch, FakeBridge([setup_dialog()]))

    with pytest.raises(PTBridgeError) as excinfo:
        server.wait_ready("R1", timeout=1.0)

    message = str(excinfo.value)
    assert "initial-configuration dialog" in message
    assert "Skip boot" in message


# --------------------------------------------------------------------------
# tools that wait
# --------------------------------------------------------------------------
def test_pt_configure_waits_then_configures(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """A booting device is awaited before anything is typed into it."""
    fake = FakeBridge([booting(), booting(), ready()])
    install_bridge(monkeypatch, fake)

    result = server.configure("R1", "hostname R1")

    assert "[fake]" in result, "the real op ran through the fake bridge on disk"
    assert [op for op, _ in fake.ops] == ["get_state", "get_state", "get_state"]


def test_pt_configure_reports_a_readiness_timeout_as_json(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    install_bridge(monkeypatch, FakeBridge([booting()]))

    result = server.configure("R1", "hostname R1", ready_timeout=1.0)

    assert '"ok": false' in result
    assert "was not ready within 1s" in result


def test_pt_configure_dry_run_does_not_wait_for_readiness(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    """PTM-16: a preview must work on a device that is still booting."""
    calls: list[str] = []

    def plan_only(op: str, args: dict | None = None, timeout: float | None = None):
        calls.append(op)
        assert op == "plan", "a dry run must not call get_state or configure"
        return {"lines": ["hostname R1"], "mode": "auto", "effective_mode": "config"}

    install_bridge(monkeypatch, plan_only)

    result = server.configure("R1", "hostname R1", dry_run=True)

    assert calls == ["plan"]
    assert '"dry_run": true' in result


def test_pt_show_waits_for_readiness(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    fake = FakeBridge([booting(), ready()])
    install_bridge(monkeypatch, fake)

    result = server.show("R1", "show version")

    assert "[fake]" in result
    assert [op for op, _ in fake.ops] == ["get_state", "get_state"]


def test_pt_show_reports_a_readiness_timeout_as_json(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    install_bridge(monkeypatch, FakeBridge([booting()]))

    result = server.show("R1", "show version", ready_timeout=1.0)

    assert '"ok": false' in result
    assert "was not ready within 1s" in result


def test_pt_configure_dry_run_surfaces_a_bridge_error(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    def missing(op: str, args: dict | None = None, timeout: float | None = None):
        raise PTBridgeError("device not found: ghost")

    install_bridge(monkeypatch, missing)

    result = server.configure("ghost", "hostname X", dry_run=True)

    assert '"ok": false' in result
    assert "device not found: ghost" in result


def test_pt_configure_dry_run_surfaces_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    def boom(op: str, args: dict | None = None, timeout: float | None = None):
        raise OSError("disk on fire")

    install_bridge(monkeypatch, boom)

    result = server.configure("R1", "hostname R1", dry_run=True)

    assert '"ok": false' in result
    assert "disk on fire" in result


def test_pt_power_ready_waits_for_the_boot(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    fake = FakeBridge([booting(), ready()])
    install_bridge(monkeypatch, fake)

    result = server.power("R1", on=True, ready=True)

    assert '"ok": true' in result
    assert len(fake.ops) == 2


def test_pt_power_without_ready_does_not_wait(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    fake = FakeBridge([booting()])
    install_bridge(monkeypatch, fake)

    server.power("R1", on=True)

    assert fake.ops == [], "no readiness poll unless ready=True"


def test_pt_power_off_never_waits(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    fake = FakeBridge([booting()])
    install_bridge(monkeypatch, fake)

    server.power("R1", on=False, ready=True)

    assert fake.ops == []


def test_wait_ready_surfaces_unexpected_errors_as_json(
    monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    def boom(op: str, args: dict | None = None, timeout: float | None = None):
        raise OSError("serial port on fire")

    install_bridge(monkeypatch, boom)

    result = server.configure("R1", "hostname R1")

    assert '"ok": false' in result
    assert "serial port on fire" in result

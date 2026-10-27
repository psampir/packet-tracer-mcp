"""Tests for the file-drop transport (:mod:`pt_mcp.protocol`) via the MCP tools.

Everything here goes through ``tests/fake_bridge.py``, which answers on disk the
way ``bridge/bridge.js`` does — so these tests prove the Python side of the
protocol, the tool wiring and the error/timeout behaviour without Packet Tracer.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pt_mcp import config, protocol, server
from pt_mcp.protocol import PTBridgeError, call_pt


# --------------------------------------------------------------------------
# transport / ping
# --------------------------------------------------------------------------
def test_ping_returns_version() -> None:
    ping = call_pt("ping")
    assert "fake" in ping["version"]


def test_ping_returns_device_count() -> None:
    ping = call_pt("ping")
    assert ping["deviceCount"] >= 0


def test_pt_status_reports_version(jload) -> None:
    status = jload(server.pt_status())
    assert "fake" in status["version"]


# --------------------------------------------------------------------------
# devices
# --------------------------------------------------------------------------
def test_pt_add_device_returns_name(jload) -> None:
    added = jload(server.pt_add_device("R1", "eRouter", "2911", 120.0, 90.0))
    assert added["name"] == "R1"


def test_pt_list_devices_includes_added(jload) -> None:
    jload(server.pt_add_device("SW1", "eSwitch", "2960-24TT"))
    listed = jload(server.pt_list_devices())
    names = {device["name"] for device in listed["devices"]}
    assert {"R1", "SW1"} <= names


def test_pt_list_devices_reports_type_and_model(jload) -> None:
    listed = jload(server.pt_list_devices())
    r1 = next(device for device in listed["devices"] if device["name"] == "R1")
    assert r1["type"] == 0
    assert r1["typeName"] == "eRouter"
    assert r1["model"] == "2911"


def test_pt_remove_device(jload) -> None:
    removed = jload(server.pt_remove_device("SW1"))
    assert removed["removed"] is True


def test_pt_remove_unknown_device_is_not_an_error(jload) -> None:
    removed = jload(server.pt_remove_device("NOPE"))
    assert removed["removed"] is False


# --------------------------------------------------------------------------
# links
# --------------------------------------------------------------------------
def test_pt_link(jload) -> None:
    jload(server.pt_add_device("SW1", "eSwitch", "2960-24TT"))
    linked = jload(server.pt_link("R1", "GigabitEthernet0/0", "SW1", "GigabitEthernet0/1"))
    assert linked["linked"] is True


def test_pt_autolink(jload) -> None:
    auto = jload(server.pt_autolink("R1", "SW1"))
    assert auto["ok"] is True


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
def test_pt_configure_returns_output(jload) -> None:
    conf = jload(
        server.pt_configure(
            "R1",
            "hostname R1\n!\ninterface GigabitEthernet0/0\n"
            " ip address 10.0.0.1 255.255.255.0\n no shutdown\n",
        )
    )
    assert "R1" in conf["output"]


def test_pt_configure_returns_prompt(jload) -> None:
    conf = jload(server.pt_configure("R1", "hostname R1"))
    assert conf["prompt"].startswith("R1")


def test_pt_configure_auto_adds_enable(jload) -> None:
    conf = jload(server.pt_configure("R1", "hostname R1"))
    assert "enable" in conf["output"]


def test_pt_configure_auto_adds_configure_terminal(jload) -> None:
    conf = jload(server.pt_configure("R1", "hostname R1"))
    assert "configure terminal" in conf["output"]


def test_pt_configure_auto_adds_end(jload) -> None:
    conf = jload(server.pt_configure("R1", "hostname R1"))
    assert "end" in conf["output"]


def test_pt_configure_accepts_list(jload) -> None:
    conf = jload(server.pt_configure("R1", ["hostname R1", "! comment", "  "]))
    assert conf["prompt"].startswith("R1")
    assert "! comment" not in conf["output"]


def test_pt_configure_rejects_empty_input(jload) -> None:
    result = jload(server.pt_configure("R1", "! only a comment\n\n"))
    assert result["ok"] is False
    assert "no commands" in result["error"]


def test_pt_show(jload) -> None:
    shown = jload(server.pt_show("R1", "show ip interface brief"))
    assert "show ip interface brief" in shown["output"]


# --------------------------------------------------------------------------
# power / save / raw
# --------------------------------------------------------------------------
def test_pt_power_on(jload) -> None:
    assert jload(server.pt_power("R1", True))["ok"] is True


def test_pt_power_off(jload) -> None:
    assert jload(server.pt_power("R1", False))["ok"] is True


def test_pt_save_default_path(jload) -> None:
    saved = jload(server.pt_save())
    assert saved["saved"] is True
    assert "/saved/" in saved["path"]


def test_pt_save_creates_saved_dir(jload, home: Path) -> None:
    jload(server.pt_save())
    assert (home / "saved").is_dir()


def test_pt_save_explicit_path(jload) -> None:
    saved = jload(server.pt_save("/tmp/lab.pkt"))
    assert saved["path"] == "/tmp/lab.pkt"


def test_pt_raw_returns_value(jload) -> None:
    raw = jload(server.pt_raw("ipc.network().getDeviceCount()"))
    assert "value" in raw


# --------------------------------------------------------------------------
# error handling
# --------------------------------------------------------------------------
def test_unknown_op_raises_bridge_error() -> None:
    with pytest.raises(PTBridgeError) as excinfo:
        call_pt("bogus_op")
    assert "unknown op" in str(excinfo.value)


def test_tool_surfaces_bridge_error_as_json(jload) -> None:
    result = jload(protocol.run_op("bogus_op"))
    assert result["ok"] is False
    assert "unknown op" in result["error"]


def test_pt_status_reports_never_set_up(monkeypatch: pytest.MonkeyPatch, jload) -> None:
    missing = Path("/nonexistent/pt-mcp-does-not-exist")
    monkeypatch.setattr(config, "BASE", missing)
    guard = jload(server.pt_status())
    assert guard["ok"] is False
    assert "never set up" in guard["error"]


def test_run_op_wraps_unexpected_exception(jload, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(protocol, "call_pt", boom)
    result = jload(protocol.run_op("ping"))
    assert result["ok"] is False
    assert "disk on fire" in result["error"]


# --------------------------------------------------------------------------
# timeouts, atomicity, cleanup
# --------------------------------------------------------------------------
def test_timeout_raises_bridge_error(dead_home: Path) -> None:
    with pytest.raises(PTBridgeError) as excinfo:
        call_pt("ping", timeout=1.0)
    assert "Timed out" in str(excinfo.value)


def test_timeout_message_mentions_packet_tracer(dead_home: Path) -> None:
    with pytest.raises(PTBridgeError) as excinfo:
        call_pt("ping", timeout=1.0)
    assert "Packet Tracer" in str(excinfo.value)


def test_timed_out_job_is_removed(dead_home: Path) -> None:
    with pytest.raises(PTBridgeError):
        call_pt("ping", timeout=0.2)
    assert list((dead_home / "jobs").glob("*.json")) == []
    assert list((dead_home / "jobs").glob("*.tmp")) == []


def test_job_file_is_written_atomically(dead_home: Path) -> None:
    """A job is only ever visible as a complete ``.json``, never as ``.tmp``."""
    job_ids: list[str] = []
    original_replace = protocol.os.replace

    def spy(src, dst):
        job_ids.append(Path(dst).name)
        result = original_replace(src, dst)
        # Renaming is atomic: the .tmp is gone and only complete .json remain.
        assert not list((dead_home / "jobs").glob("*.tmp"))
        assert (dead_home / "jobs" / Path(dst).name).is_file()
        return result

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(protocol.os, "replace", spy)
        with pytest.raises(PTBridgeError):
            call_pt("ping", timeout=0.2)

    assert len(job_ids) == 1
    assert job_ids[0].endswith(".json")


def test_successful_call_cleans_up_both_files(home: Path) -> None:
    before = set((home / "results").glob("*.json"))
    call_pt("ping")
    after = set((home / "results").glob("*.json"))
    assert after <= before
    assert list((home / "jobs").glob("*.json")) == []


def test_stale_result_is_ignored(dead_home: Path) -> None:
    """A leftover result for a different id must not be mistaken for ours."""
    results = dead_home / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "deadbeef.json").write_text(
        json.dumps({"id": "someone-else", "ok": True, "result": {"version": "stale"}}),
        encoding="utf-8",
    )
    started = time.monotonic()
    with pytest.raises(PTBridgeError):
        call_pt("ping", timeout=0.3)
    assert time.monotonic() - started >= 0.3

"""Tests for ``packet-tracer-mcp --doctor`` (:mod:`pt_mcp.doctor`).

The doctor only reads the filesystem, so every case here is a temporary home:
one live mailbox, an empty one, several live ones, and the environment override.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pt_mcp import config, doctor, server


def _live(path: Path) -> Path:
    (path / "jobs").mkdir(parents=True)
    (path / "results").mkdir()
    return path


def _box(tmp_path: Path, **env: str) -> config.Mailbox:
    return config.inspect(home=tmp_path, environ=env)


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------
def test_report_lists_every_candidate_with_its_state(tmp_path: Path) -> None:
    live = _live(tmp_path / "pt" / "pt-mcp")
    empty = tmp_path / "Documents" / "pt-mcp"
    empty.mkdir(parents=True)
    absent = tmp_path / "Library" / "Application Support" / "Packet Tracer 9.0.1" / "pt-mcp"

    text = doctor.report(_box(tmp_path), environ={}, now=0.0)

    assert f"mailbox: {live}" in text
    assert f"  live    {live}" in text
    assert f"  empty   {empty}" in text
    assert "exists, but jobs/ or results/ is missing" in text
    assert "no traffic yet" in text
    assert str(absent) not in text, "a pattern with no match contributes no candidate"


def test_report_counts_results_and_unanswered_jobs(tmp_path: Path) -> None:
    live = _live(tmp_path / "pt" / "pt-mcp")
    answered = live / "results" / "done.json"
    answered.write_text("{}", encoding="utf-8")
    os.utime(answered, (0, 0))
    (live / "jobs" / "pending.json").write_text("{}", encoding="utf-8")

    text = doctor.report(_box(tmp_path), environ={}, now=30)

    assert "1 result(s), last answered 30s ago" in text
    assert "1 unanswered job(s)" in text


def test_age_uses_readable_units(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    marker.write_text("", encoding="utf-8")
    os.utime(marker, (0, 0))

    assert doctor._age(marker, 30) == "30s ago"
    assert doctor._age(marker, 600) == "10m ago"
    assert doctor._age(marker, 7200) == "2h ago"
    assert doctor._age(marker, 3 * 86400) == "3d ago"


def test_report_resolves_a_mailbox_when_none_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = _box(tmp_path)
    monkeypatch.setattr(config, "inspect", lambda *a, **k: box)

    assert f"mailbox: {box.path}" in doctor.report(now=0.0)


# --------------------------------------------------------------------------
# hints
# --------------------------------------------------------------------------
def test_hint_for_a_detected_live_mailbox(tmp_path: Path) -> None:
    _live(tmp_path / "pt" / "pt-mcp")

    text = doctor.report(_box(tmp_path), environ={})

    assert "the bridge is running here; nothing to do." in text
    assert "the first candidate that Packet Tracer has already created" in text


def test_hint_warns_about_several_live_mailboxes(tmp_path: Path) -> None:
    _live(tmp_path / "pt" / "pt-mcp")
    _live(tmp_path / "Documents" / "pt-mcp")

    text = doctor.report(_box(tmp_path), environ={})

    assert "2 live mailboxes exist" in text
    assert "Set PT_MCP_HOME" not in text


def test_hint_when_no_mailbox_exists_yet(tmp_path: Path) -> None:
    text = doctor.report(_box(tmp_path), environ={})

    assert "PT_MCP_HOME: not set" in text
    assert "no mailbox exists yet" in text
    assert "the fallback" in text


def test_hint_when_the_environment_points_nowhere(tmp_path: Path) -> None:
    env = {"PT_MCP_HOME": str(tmp_path / "nope")}

    text = doctor.report(_box(tmp_path, **env), environ=env)

    assert f"PT_MCP_HOME: {env['PT_MCP_HOME']}" in text
    assert "set but that mailbox is not there yet" in text


def test_hint_for_a_live_environment_mailbox(tmp_path: Path) -> None:
    live = _live(tmp_path / "custom")
    env = {"PT_MCP_HOME": str(live)}

    text = doctor.report(_box(tmp_path, **env), environ=env)

    assert "PT_MCP_HOME, set explicitly" in text
    assert "the bridge is running here; nothing to do." in text


# --------------------------------------------------------------------------
# exit codes and the CLI surface
# --------------------------------------------------------------------------
def test_main_exits_zero_with_a_live_mailbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    box = _box(tmp_path)
    _live(box.path)
    monkeypatch.setattr(config, "inspect", lambda *a, **k: box)

    assert doctor.main() == 0
    assert str(box.path) in capsys.readouterr().out


def test_main_exits_one_without_a_mailbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = _box(tmp_path)
    monkeypatch.setattr(config, "inspect", lambda *a, **k: box)

    assert doctor.main() == 1


def test_server_doctor_flag_prints_and_exits_with_the_doctor_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor, "main", lambda: 7)
    monkeypatch.setattr(server.server, "run", lambda *a, **k: pytest.fail("started"))

    with pytest.raises(SystemExit) as exit_info:
        server.main(["--doctor"])

    assert exit_info.value.code == 7


def test_server_starts_the_stdio_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    box = config.Mailbox(path=tmp_path / "only", source="default", candidates=())
    monkeypatch.setattr(config, "MAILBOX", box)
    started: list[str] = []
    monkeypatch.setattr(server.server, "run", started.append)

    server.main([])

    assert started == ["stdio"]
    assert capsys.readouterr().err == "", "a single mailbox must not warn"


def test_server_warns_about_several_live_mailboxes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _live(tmp_path / "pt" / "pt-mcp")
    second = _live(tmp_path / "Documents" / "pt-mcp")
    monkeypatch.setattr(config, "MAILBOX", _box(tmp_path))
    started: list[str] = []
    monkeypatch.setattr(server.server, "run", started.append)

    server.main([])

    assert started == ["stdio"]
    warning = capsys.readouterr().err
    assert "several live mailboxes" in warning
    assert str(first) in warning
    assert str(second) in warning
    assert "Set PT_MCP_HOME" in warning

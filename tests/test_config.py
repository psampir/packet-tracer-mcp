"""Tests for the path resolution and tunables in :mod:`pt_mcp.config`.

``config`` reads the environment once at import time, so the resolution is
exercised through :func:`config.inspect` and :func:`config.candidate_homes` with
a temporary home and an explicit environment, exactly as the server calls them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pt_mcp import config

ROOT = Path(__file__).resolve().parents[1]


def _live(path: Path) -> Path:
    """Create the two directories ``bridge.js`` makes when it starts."""
    (path / "jobs").mkdir(parents=True)
    (path / "results").mkdir()
    return path


def test_default_home_is_the_pt_user_folder() -> None:
    expected = str(Path.home() / "pt" / "pt-mcp")
    assert expected == config.DEFAULT_HOME


def test_bridge_derives_the_same_home_from_pt() -> None:
    """The module must not hard-code a home directory: it asks PT for one."""
    bridge = (ROOT / "bridge" / "bridge.js").read_text(encoding="utf-8")
    line = re.search(r"^var PT_MCP_HOME = (.+);$", bridge, re.MULTILINE)

    assert line is not None, "bridge.js must define PT_MCP_HOME at the top"
    expression = line.group(1)
    assert "getUserFolder()" in expression
    assert expression.endswith('+ "/pt-mcp"')
    assert str(Path.home()) not in bridge


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------
def test_candidates_start_with_the_linux_profile(tmp_path: Path) -> None:
    candidates = config.candidate_homes(tmp_path)

    assert candidates[0] == tmp_path / "pt" / "pt-mcp"
    assert tmp_path / "Documents" / "pt-mcp" in candidates


def test_candidates_expand_versioned_profile_folders(tmp_path: Path) -> None:
    older = _live(tmp_path / "Documents" / "Packet Tracer 8.2.2" / "pt-mcp")
    newer = _live(tmp_path / "Documents" / "Packet Tracer 9.0.1" / "pt-mcp")

    candidates = config.candidate_homes(tmp_path)

    assert older in candidates
    assert newer in candidates
    assert candidates.index(older) < candidates.index(newer)


def test_candidates_cover_the_macos_profile(tmp_path: Path) -> None:
    path = _live(
        tmp_path / "Library" / "Application Support" / "Packet Tracer 9.0.1" / "pt-mcp"
    )

    assert path in config.candidate_homes(tmp_path)


def test_candidates_for_a_bare_home_are_the_patterns(tmp_path: Path) -> None:
    candidates = config.candidate_homes(tmp_path)

    # Nothing exists under tmp_path, so only the literal patterns show up.
    assert candidates == (
        tmp_path / "pt" / "pt-mcp",
        tmp_path / "Documents" / "pt-mcp",
    )


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------
def test_environment_wins_and_skips_the_search(tmp_path: Path) -> None:
    explicit = tmp_path / "custom"
    box = config.inspect(home=tmp_path, environ={"PT_MCP_HOME": str(explicit)})

    assert box.path == explicit
    assert box.source == "environment"
    assert box.live == ()
    assert box.candidates, "--doctor still reports what the search would have tried"


def test_environment_expands_a_tilde(tmp_path: Path) -> None:
    box = config.inspect(home=tmp_path, environ={"PT_MCP_HOME": "~/mailbox"})

    assert box.path == Path.home() / "mailbox"


def test_a_live_candidate_is_detected(tmp_path: Path) -> None:
    detected = _live(tmp_path / "Documents" / "pt-mcp")

    box = config.inspect(home=tmp_path, environ={})

    assert box.path == detected
    assert box.source == "detected"
    assert box.live == (detected,)


def test_the_first_live_candidate_wins(tmp_path: Path) -> None:
    first = _live(tmp_path / "pt" / "pt-mcp")
    second = _live(tmp_path / "Documents" / "pt-mcp")

    box = config.inspect(home=tmp_path, environ={})

    assert box.path == first
    assert box.live == (first, second)


def test_nothing_live_falls_back_to_the_first_candidate(tmp_path: Path) -> None:
    box = config.inspect(home=tmp_path, environ={})

    assert box.path == tmp_path / "pt" / "pt-mcp"
    assert box.source == "default"
    assert box.live == ()


def test_is_live_requires_both_directories(tmp_path: Path) -> None:
    (tmp_path / "jobs").mkdir()

    assert config.is_live(tmp_path) is False
    (tmp_path / "results").mkdir()
    assert config.is_live(tmp_path) is True


def test_other_live_mailbox_reports_a_different_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live(tmp_path / "pt" / "pt-mcp")
    second = _live(tmp_path / "Documents" / "pt-mcp")
    monkeypatch.setattr(config, "MAILBOX", config.inspect(home=tmp_path, environ={}))

    assert config.other_live_mailbox() == second


def test_other_live_mailbox_is_none_when_only_the_current_one_lives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live(tmp_path / "pt" / "pt-mcp")
    monkeypatch.setattr(config, "MAILBOX", config.inspect(home=tmp_path, environ={}))

    assert config.other_live_mailbox() is None


def test_other_live_mailbox_compares_against_an_explicit_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = _live(tmp_path / "pt" / "pt-mcp")
    monkeypatch.setattr(config, "MAILBOX", config.inspect(home=tmp_path, environ={}))

    assert config.other_live_mailbox(tmp_path / "elsewhere") == live


# --------------------------------------------------------------------------
# timeout (unchanged behaviour)
# --------------------------------------------------------------------------
def test_timeout_defaults_to_the_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PT_MCP_TIMEOUT", raising=False)
    assert config._timeout_from_env() == config.DEFAULT_TIMEOUT


def test_timeout_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PT_MCP_TIMEOUT", "7.5")
    assert config._timeout_from_env() == 7.5


def test_unparsable_timeout_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PT_MCP_TIMEOUT", "soon")
    assert config._timeout_from_env() == config.DEFAULT_TIMEOUT

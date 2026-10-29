"""Tests for the paths and tunables in :mod:`pt_mcp.config`.

``config`` reads the environment once at import time, so the ``PT_MCP_TIMEOUT``
parsing is exercised through its function (``_timeout_from_env``) with a patched
environment rather than by re-importing the module.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pt_mcp import config

ROOT = Path(__file__).resolve().parents[1]


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

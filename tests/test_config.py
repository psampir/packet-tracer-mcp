"""Tests for the paths and tunables in :mod:`pt_mcp.config`.

``config`` reads the environment once at import time, so the ``PT_MCP_TIMEOUT``
parsing is exercised through its function (``_timeout_from_env``) with a patched
environment rather than by re-importing the module.
"""

from __future__ import annotations

import pytest

from pt_mcp import config


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

"""Shared pytest fixtures: an isolated ``PT_MCP_HOME`` and a running fake bridge.

The whole suite is offline — no Packet Tracer, no network. ``tests/fake_bridge.py``
plays the part of ``bridge/bridge.js``: it polls ``<home>/jobs`` and writes one
canned response per job into ``<home>/results``, with the same file semantics.

``PT_MCP_HOME`` must be set **before** ``pt_mcp.config`` is imported (the paths
are module-level constants), so the environment is configured here, at import
time, from a throwaway directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

#: Throwaway base directory for the whole session.
TMP = Path(tempfile.mkdtemp(prefix="pt-mcp-test-"))

#: Fake bridge start-up budget, in seconds.
READY_TIMEOUT = 10.0

os.environ["PT_MCP_HOME"] = str(TMP)
os.environ["PT_MCP_TIMEOUT"] = "15"

from pt_mcp import config, protocol, server  # noqa: E402  (after the env is set)
from pt_mcp.commands import normalize_commands  # noqa: E402


def _start_fake_bridge() -> subprocess.Popen:
    """Boot ``tests/fake_bridge.py`` against :data:`TMP` and wait until it is ready."""
    env = dict(os.environ)
    env["PT_MCP_HOME"] = str(TMP)
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).parent / "fake_bridge.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if (TMP / "jobs").is_dir() and (TMP / "results").is_dir():
            time.sleep(0.1)
            return proc
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"fake_bridge exited early:\n{output}")
        time.sleep(0.05)

    proc.terminate()
    raise RuntimeError("fake_bridge did not become ready in time")


@pytest.fixture(scope="session")
def bridge() -> Iterator[subprocess.Popen]:
    """The fake bridge, running for the whole session (it keeps device state)."""
    proc = _start_fake_bridge()
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(TMP, ignore_errors=True)


@pytest.fixture(autouse=True)
def _bridge_running(bridge: subprocess.Popen) -> None:
    """Every test in this suite is a transport test: make sure the bridge is up."""
    assert bridge.poll() is None, "fake_bridge died during the session"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the session home in place; only per-test overrides are allowed."""
    monkeypatch.setattr(config, "BASE", TMP, raising=True)
    monkeypatch.setattr(config, "JOBS", TMP / "jobs", raising=True)
    monkeypatch.setattr(config, "RESULTS", TMP / "results", raising=True)
    monkeypatch.setattr(config, "SAVED", TMP / "saved", raising=True)


@pytest.fixture
def home() -> Path:
    """The session's ``PT_MCP_HOME``."""
    return TMP


@pytest.fixture
def dead_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home directory nobody answers in — for timeout and setup errors."""
    home = tmp_path / "silent-home"
    home.mkdir()
    monkeypatch.setattr(config, "BASE", home)
    monkeypatch.setattr(config, "JOBS", home / "jobs")
    monkeypatch.setattr(config, "RESULTS", home / "results")
    monkeypatch.setattr(config, "SAVED", home / "saved")
    return home


@pytest.fixture
def jload() -> Callable[[str], object]:
    """Parse the JSON string an MCP tool returned."""

    def _load(payload: str):
        return json.loads(payload)

    return _load


@pytest.fixture
def modules() -> dict[str, ModuleType]:
    """The modules under test, for tests that patch transport internals."""
    return {"config": config, "protocol": protocol, "server": server}


@pytest.fixture
def normalize() -> Callable[[str | list[str]], list[str]]:
    """The command normalizer under test."""
    return normalize_commands

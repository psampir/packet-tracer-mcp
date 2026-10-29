"""Where a scripted bridge and a fake clock have to be installed after the split.

``pt_mcp.server`` used to hold every tool body, so one ``monkeypatch.setattr`` on
``server.call_pt`` / ``server.time`` reached all of it. The bodies now live in
several modules and each one holds its own reference to ``call_pt`` and ``time``,
so a scripted transport has to be installed in every one of them. These helpers
are that single list, kept in step with the modules that call the bridge.

The ``run_op()``-based tools are the exception: they always went through
``pt_mcp.protocol``'s own reference, which the old patch never reached. Pass
``transport=True`` when a test wants those tools scripted as well.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

import pytest

from pt_mcp import eval as pt_eval
from pt_mcp import hosts, protocol, readiness
from pt_mcp.tools import build as tools_build
from pt_mcp.tools import cli as tools_cli

#: Modules that hold their own ``call_pt`` reference — the ones the old
#: ``pt_mcp.server`` namespace used to cover with a single patch.
BRIDGE_MODULES: tuple[ModuleType, ...] = (
    pt_eval,
    hosts,
    readiness,
    tools_build,
    tools_cli,
)

#: Modules that wait with ``time.sleep`` / ``time.monotonic`` themselves.
CLOCK_MODULES: tuple[ModuleType, ...] = (readiness, hosts)


def install_bridge(
    monkeypatch: pytest.MonkeyPatch,
    fake: Callable[..., object],
    *,
    transport: bool = False,
) -> Callable[..., object]:
    """Point every module that holds a ``call_pt`` reference at ``fake``."""
    modules = BRIDGE_MODULES + ((protocol,) if transport else ())
    for module in modules:
        monkeypatch.setattr(module, "call_pt", fake)
    return fake


def install_clock(monkeypatch: pytest.MonkeyPatch, fake: object) -> object:
    """Point every waiting module at ``fake`` (a stand-in for :mod:`time`)."""
    for module in CLOCK_MODULES:
        monkeypatch.setattr(module, "time", fake)
    return fake

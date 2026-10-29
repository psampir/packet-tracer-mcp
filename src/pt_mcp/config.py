"""Paths and tunables for the Packet Tracer bridge.

Everything the transport needs is derived from one directory, ``PT_MCP_HOME``
(default ``~/pt/pt-mcp``) so a second Packet Tracer install —
or a test run — only has to point the environment variable somewhere else:

===============  ==========================================================
``jobs/``        requests written by this server, consumed by ``bridge.js``
``results/``     responses written by ``bridge.js``, consumed by this server
``saved/``       default target for ``pt_save`` (timestamped ``.pkt`` copies)
===============  ==========================================================

``PT_MCP_TIMEOUT`` (seconds, default 120) bounds every job. The default lives in
:data:`TIMEOUT`; each call may override it.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Directory the PT Script Module and this server agree on. ``bridge.js`` derives
#: it from Packet Tracer's user folder (``getUserFolder()`` + ``/pt-mcp``), which
#: is ``~/pt/pt-mcp`` on Linux; keep the two in step.
DEFAULT_HOME = str(Path.home() / "pt" / "pt-mcp")

#: How long a job may take before the transport gives up, in seconds.
DEFAULT_TIMEOUT = 120.0

#: Polling interval while waiting for a result file, in seconds.
POLL_INTERVAL = 0.05


def _timeout_from_env() -> float:
    """Read ``PT_MCP_TIMEOUT``; fall back to :data:`DEFAULT_TIMEOUT` when unparsable."""
    raw = os.environ.get("PT_MCP_TIMEOUT")
    if raw is None:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT


#: Shared base directory (override with ``PT_MCP_HOME``).
BASE = Path(os.environ.get("PT_MCP_HOME", DEFAULT_HOME)).expanduser()

#: Job queue watched by ``bridge.js``.
JOBS = BASE / "jobs"

#: Response directory written by ``bridge.js``.
RESULTS = BASE / "results"

#: Default location for ``pt_save`` snapshots.
SAVED = BASE / "saved"

#: Default per-job timeout, in seconds.
TIMEOUT = _timeout_from_env()

__all__ = [
    "BASE",
    "DEFAULT_HOME",
    "DEFAULT_TIMEOUT",
    "JOBS",
    "POLL_INTERVAL",
    "RESULTS",
    "SAVED",
    "TIMEOUT",
]

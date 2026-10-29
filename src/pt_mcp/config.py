"""Paths and tunables for the Packet Tracer bridge.

Everything the transport needs is derived from one directory, the *mailbox*
(``PT_MCP_HOME``), so a second Packet Tracer install — or a test run — only has
to point the environment variable somewhere else:

===============  ==========================================================
``jobs/``        requests written by this server, consumed by ``bridge.js``
``results/``     responses written by ``bridge.js``, consumed by this server
``saved/``       default target for ``pt_save`` (timestamped ``.pkt`` copies)
===============  ==========================================================

``bridge.js`` computes the mailbox itself, as ``getUserFolder() + "/pt-mcp"``
inside Packet Tracer. This side has to find the same directory without asking
PT, so :func:`inspect` walks :data:`HOME_PATTERNS` in order and takes the first
candidate that already holds both ``jobs/`` and ``results/`` — the ones the
bridge creates when it starts. ``PT_MCP_HOME`` always wins and skips the search;
when nothing matches yet, the first candidate stands in so the error messages
name the expected location.

``PT_MCP_TIMEOUT`` (seconds, default 120) bounds every job. The default lives in
:data:`TIMEOUT`; each call may override it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: Mailboxes tried in order, relative to the user's home directory. Each pattern
#: is what ``bridge.js`` computes for one Packet Tracer build: PT keeps its
#: profile in ``~/pt`` on Linux and in Documents / Application Support on Windows
#: and macOS, with the ``*`` covering versioned profile folders. Order matters:
#: the first candidate that looks alive wins.
HOME_PATTERNS = (
    "pt/pt-mcp",
    "Documents/pt-mcp",
    "Documents/Packet Tracer*/pt-mcp",
    "Library/Application Support/Packet Tracer*/pt-mcp",
)

#: The Linux default, i.e. the first candidate for a home directory.
DEFAULT_HOME = str(Path.home() / "pt" / "pt-mcp")

#: How long a job may take before the transport gives up, in seconds.
DEFAULT_TIMEOUT = 120.0

#: Polling interval while waiting for a result file, in seconds.
POLL_INTERVAL = 0.05


@dataclass(frozen=True)
class Mailbox:
    """The resolved mailbox, plus everything needed to explain the choice."""

    #: Directory in use (holds ``jobs/``, ``results/`` and ``saved/``).
    path: Path
    #: ``environment`` (PT_MCP_HOME), ``detected`` (a live candidate) or
    #: ``default`` (nothing exists yet, so the first candidate stands in).
    source: str
    #: Every candidate the search considered, in order.
    candidates: tuple[Path, ...] = ()
    #: Candidates that already look like a running bridge.
    live: tuple[Path, ...] = ()


def candidate_homes(home: Path | None = None) -> tuple[Path, ...]:
    """Return the mailbox candidates for ``home``, in :data:`HOME_PATTERNS` order."""
    base = Path(home) if home is not None else Path.home()
    found: list[Path] = []
    for pattern in HOME_PATTERNS:
        matches = sorted(base.glob(pattern)) if "*" in pattern else [base / pattern]
        for match in matches:
            if match not in found:
                found.append(match)
    return tuple(found)


def is_live(path: Path) -> bool:
    """True when ``bridge.js`` has already created this mailbox (both subdirs)."""
    path = Path(path)
    return (path / "jobs").is_dir() and (path / "results").is_dir()


def inspect(
    home: Path | None = None, environ: Mapping[str, str] | None = None
) -> Mailbox:
    """Resolve the mailbox: ``PT_MCP_HOME`` first, then the first live candidate."""
    env = os.environ if environ is None else environ
    candidates = candidate_homes(home)

    explicit = env.get("PT_MCP_HOME")
    if explicit:
        return Mailbox(Path(explicit).expanduser(), "environment", candidates)

    live = tuple(path for path in candidates if is_live(path))
    if live:
        return Mailbox(live[0], "detected", candidates, live)
    return Mailbox(candidates[0], "default", candidates)


def _timeout_from_env() -> float:
    """Read ``PT_MCP_TIMEOUT``; fall back to :data:`DEFAULT_TIMEOUT` when unparsable."""
    raw = os.environ.get("PT_MCP_TIMEOUT")
    if raw is None:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT


#: Resolution performed once at import, from the real environment.
MAILBOX = inspect()

#: Shared base directory (override with ``PT_MCP_HOME``).
BASE = MAILBOX.path


def other_live_mailbox(current: Path | None = None) -> Path | None:
    """Return a live candidate other than ``current``, scanning the disk afresh.

    Used by the failure text: the client can start before the bridge, so the
    module may have created its directory after this process resolved the path.
    """
    current_path = Path(current) if current is not None else MAILBOX.path
    for path in MAILBOX.candidates:
        if path != current_path and is_live(path):
            return path
    return None


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
    "HOME_PATTERNS",
    "JOBS",
    "MAILBOX",
    "Mailbox",
    "POLL_INTERVAL",
    "RESULTS",
    "SAVED",
    "TIMEOUT",
    "candidate_homes",
    "inspect",
    "is_live",
    "other_live_mailbox",
]

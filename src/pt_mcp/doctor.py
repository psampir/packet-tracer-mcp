"""``packet-tracer-mcp --doctor``: which mailbox was resolved, and why.

A support aid, not a tool: it prints the directory the server uses, every
candidate Packet Tracer might have created, and what the bridge has done there.
It never talks to Packet Tracer, so it works with the Script Module stopped, and
its output is the first thing to paste into a bug report.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path

from . import __version__, config

#: How each resolution source is described to the user.
_SOURCES = {
    "environment": "PT_MCP_HOME, set explicitly (the search is skipped)",
    "detected": "the first candidate that Packet Tracer has already created",
    "default": "the fallback: no candidate exists yet",
}

_NO_MAILBOX = (
    "no mailbox exists yet. Start the Script Module once (Extensions -> Scripting ->\n"
    "Configure PT Script Modules... -> Start) and run --doctor again. If PT is already\n"
    "running, its log line '[pt-mcp] bridge ready (home=...)' names the directory;\n"
    "set PT_MCP_HOME to that path on both sides if it is not a candidate below."
)


def _age(path: Path, now: float) -> str:
    seconds = max(0.0, now - path.stat().st_mtime)
    if seconds < 60:
        return f"{seconds:.0f}s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 86400:
        return f"{seconds / 3600:.0f}h ago"
    return f"{seconds / 86400:.0f}d ago"


def _traffic(path: Path, now: float) -> str:
    """Describe the files in one mailbox: answered, unanswered, or empty."""
    results = sorted((path / "results").glob("*.json"), key=lambda p: p.stat().st_mtime)
    jobs = sorted((path / "jobs").glob("*.json"), key=lambda p: p.stat().st_mtime)
    parts: list[str] = []
    if results:
        parts.append(f"{len(results)} result(s), last answered {_age(results[-1], now)}")
    if jobs:
        parts.append(f"{len(jobs)} unanswered job(s)")
    return ", ".join(parts) or "no traffic yet"


def _state(path: Path, now: float) -> tuple[str, str]:
    if config.is_live(path):
        return "live  ", _traffic(path, now)
    if path.exists():
        return "empty ", "exists, but jobs/ or results/ is missing"
    return "absent", "not created yet"


def _hint(box: config.Mailbox, now: float) -> str:
    if config.is_live(box.path):
        if len(box.live) > 1:
            return (
                f"{len(box.live)} live mailboxes exist; using {box.live[0]}. "
                "Remove the stale one, or set PT_MCP_HOME to the right one."
            )
        return "the bridge is running here; nothing to do."
    if box.source == "environment":
        return (
            "PT_MCP_HOME is set but that mailbox is not there yet. Start the Script "
            "Module once, or unset PT_MCP_HOME to let the search decide."
        )
    return _NO_MAILBOX


def report(
    mailbox: config.Mailbox | None = None,
    environ: Mapping[str, str] | None = None,
    now: float | None = None,
) -> str:
    """Render the doctor output for ``mailbox`` (default: resolve a fresh one)."""
    box = config.inspect() if mailbox is None else mailbox
    env = os.environ if environ is None else environ
    moment = time.time() if now is None else now

    lines = [
        f"packet-tracer-mcp {__version__}",
        f"mailbox: {box.path}",
        f"source:  {_SOURCES.get(box.source, box.source)}",
        "",
        f"PT_MCP_HOME: {env.get('PT_MCP_HOME') or 'not set'}",
        "",
        "candidate mailboxes, in order:",
    ]
    for candidate in box.candidates:
        mark, detail = _state(candidate, moment)
        lines.append(f"  {mark}  {candidate}  ({detail})")
    lines += ["", "hint:", *[f"  {line}" for line in _hint(box, moment).splitlines()]]
    return "\n".join(lines) + "\n"


def main() -> int:
    """Print the report; exit 0 when a live mailbox was found, 1 otherwise."""
    box = config.inspect()
    print(report(box), end="")
    return 0 if config.is_live(box.path) else 1


__all__ = ["main", "report"]

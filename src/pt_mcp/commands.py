"""CLI input cleanup for the ``pt_configure`` tool — split and strip only.

This module used to carry the *mode* policy (prepending ``enable`` and
``configure terminal``, appending ``end``). That decision moved into
``bridge/bridge.js`` (PTM-02), because only the bridge can see the prompt after
every line — and because a PC's ``Command Prompt`` must not receive a single
injected line, which the server could not know.

What remains here is transport-agnostic tidying:

* accept a newline-separated **string** or a **list** of strings (list items may
  themselves be multi-line, e.g. pasted config blocks);
* drop blank lines and ``!`` comment lines;
* strip surrounding whitespace from every line;
* raise :class:`ValueError` when nothing is left.

Which wrappers (if any) are added is decided by the bridge, per device and per
prompt: see ``docs/cli-semantics.md``.
"""

from __future__ import annotations

__all__ = ["normalize_commands"]


def normalize_commands(commands: str | list[str]) -> list[str]:
    """Split, strip and de-comment CLI input, preserving the order given.

    Raises :class:`ValueError` when nothing is left after stripping blanks and
    comments, so the caller can answer the tool with an actionable error instead
    of sending an empty command list to Packet Tracer.
    """
    if isinstance(commands, str):
        raw_lines = commands.splitlines()
    else:
        raw_lines = []
        for item in commands:
            raw_lines.extend(str(item).splitlines())

    cleaned: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue
        cleaned.append(stripped)

    if not cleaned:
        raise ValueError("pt_configure: no commands after stripping blanks/comments")

    return cleaned

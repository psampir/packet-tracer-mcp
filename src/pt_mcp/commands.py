"""CLI command normalization for the ``pt_configure`` tool.

An agent usually sends the interesting lines only — "hostname R1", an interface
block, ``no shutdown`` — leaving out the modal wrappers. Packet Tracer's console
line, unlike a human at the terminal, is unforgiving about that: commands are
typed into whatever mode the device is currently in. :func:`normalize_commands`
therefore makes the sequence self-contained before it leaves the server:

* accepts a newline-separated **string** or a **list** of strings (list items may
  themselves be multi-line, e.g. pasted config blocks);
* drops blank lines and ``!`` comment lines;
* prepends ``enable`` and ``configure terminal`` when neither form is present
  (``en``, ``conf t``, ``config t`` and ``configure t`` all count);
* appends ``end`` when the sequence does not already finish with it.
"""

from __future__ import annotations

_ENABLE_WORDS = {"enable", "en"}
_CONF_WORDS = {"configure terminal", "conf t", "config t", "configure t", "configure"}

__all__ = ["normalize_commands"]


def normalize_commands(commands: str | list[str]) -> list[str]:
    """Normalize CLI input into an ordered list ready for the device.

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

    lowered = [command.lower() for command in cleaned]

    prefix: list[str] = []
    if not any(command in _ENABLE_WORDS for command in lowered):
        prefix.append("enable")
    if not any(command in _CONF_WORDS for command in lowered):
        prefix.append("configure terminal")

    final = prefix + cleaned
    if final[-1].lower() != "end":
        final.append("end")
    return final

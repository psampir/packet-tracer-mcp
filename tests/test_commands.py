"""Tests for :func:`pt_mcp.commands.normalize_commands`.

The normalizer is deliberately dumb: it splits input into lines, drops blanks and
``!`` comments and strips whitespace. Every mode decision (``enable``,
``configure terminal``, ``end``) belongs to ``bridge/bridge.js``, because only the
bridge sees the prompt — see ``docs/cli-semantics.md`` and ``tests/test_ready.py``.
"""

from __future__ import annotations

import pytest

from pt_mcp.commands import normalize_commands


def test_string_input_is_split_into_lines() -> None:
    assert normalize_commands("hostname R1\nvlan 10") == ["hostname R1", "vlan 10"]


def test_list_input_is_accepted() -> None:
    assert normalize_commands(["hostname R1", "no shutdown"]) == ["hostname R1", "no shutdown"]


def test_list_items_may_be_multiline() -> None:
    assert normalize_commands(["interface GigabitEthernet0/0\n no shutdown"]) == [
        "interface GigabitEthernet0/0",
        "no shutdown",
    ]


def test_blank_lines_and_comments_are_dropped() -> None:
    lines = normalize_commands(["! a comment", "", "  ", "show run"])
    assert lines == ["show run"]


def test_surrounding_whitespace_is_stripped() -> None:
    assert normalize_commands("   hostname R1   \n") == ["hostname R1"]


def test_no_mode_policy_is_applied() -> None:
    """enable / configure terminal / end are the bridge's job, not ours."""
    assert normalize_commands("hostname R1") == ["hostname R1"]
    assert "enable" not in normalize_commands("hostname R1")
    assert "configure terminal" not in normalize_commands("hostname R1")
    assert "end" not in normalize_commands("hostname R1")


def test_explicit_wrapper_lines_are_preserved_verbatim() -> None:
    assert normalize_commands("enable\nconf t\nhostname R1\nend") == [
        "enable",
        "conf t",
        "hostname R1",
        "end",
    ]


def test_order_and_duplicates_are_preserved() -> None:
    assert normalize_commands(["no shutdown", "no shutdown"]) == ["no shutdown", "no shutdown"]


def test_only_comments_raise_value_error() -> None:
    with pytest.raises(ValueError, match="no commands"):
        normalize_commands("! nothing here\n\n   \n")


def test_empty_input_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no commands"):
        normalize_commands("")


def test_empty_list_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no commands"):
        normalize_commands([])

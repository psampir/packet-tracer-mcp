"""Tests for :func:`pt_mcp.commands.normalize_commands`.

The normalizer is what makes an agent's friendly command list safe to type into a
device that is sitting in an unknown CLI mode, so each rule has its own test.
"""

from __future__ import annotations

import pytest

from pt_mcp.commands import normalize_commands


def test_prepends_enable_and_configure_terminal_and_appends_end() -> None:
    assert normalize_commands("hostname R1") == [
        "enable",
        "configure terminal",
        "hostname R1",
        "end",
    ]


def test_existing_enable_configure_and_end_are_kept_once() -> None:
    assert normalize_commands("enable\nconfigure terminal\nhostname R1\nend") == [
        "enable",
        "configure terminal",
        "hostname R1",
        "end",
    ]


def test_list_input_is_accepted() -> None:
    assert normalize_commands(["hostname R1", "no shutdown"]) == [
        "enable",
        "configure terminal",
        "hostname R1",
        "no shutdown",
        "end",
    ]


def test_list_items_may_be_multiline() -> None:
    assert normalize_commands(["interface GigabitEthernet0/0\n no shutdown"]) == [
        "enable",
        "configure terminal",
        "interface GigabitEthernet0/0",
        "no shutdown",
        "end",
    ]


def test_blank_lines_and_comments_are_dropped() -> None:
    lines = normalize_commands(["! a comment", "", "  ", "show run"])
    assert "show run" in lines
    assert "! a comment" not in lines
    assert "" not in lines


def test_surrounding_whitespace_is_stripped() -> None:
    assert normalize_commands("   hostname R1   \n") == [
        "enable",
        "configure terminal",
        "hostname R1",
        "end",
    ]


def test_abbreviations_count_as_enable_and_configure() -> None:
    assert normalize_commands("en\nconf t\nhostname R1") == [
        "en",
        "conf t",
        "hostname R1",
        "end",
    ]


def test_lowercase_end_is_not_duplicated() -> None:
    assert normalize_commands("hostname R1\nend") == [
        "enable",
        "configure terminal",
        "hostname R1",
        "end",
    ]


def test_only_comments_raise_value_error() -> None:
    with pytest.raises(ValueError, match="no commands"):
        normalize_commands("! nothing here\n\n   \n")


def test_empty_input_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no commands"):
        normalize_commands("")

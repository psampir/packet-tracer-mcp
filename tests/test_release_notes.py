"""Tests for tools/release_notes.py (the release body comes from CHANGELOG)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "release_notes.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_extracts_only_the_requested_section(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [0.1.0] - 2026-01-01\n\n### Added\n\n- first\n\n"
        "## [0.2.0] - 2026-02-01\n\n### Added\n\n- second\n",
        encoding="utf-8",
    )

    result = run("v0.1.0", "--changelog", str(changelog))

    assert result.returncode == 0
    assert result.stdout.startswith("# v0.1.0")
    assert "- first" in result.stdout
    assert "0.2.0" not in result.stdout


def test_accepts_a_bare_version(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [0.3.1] - 2026-01-01\n\n### Fixed\n\n- thing\n", encoding="utf-8")

    result = run("0.3.1", "--changelog", str(changelog))

    assert result.returncode == 0
    assert "# v0.3.1" in result.stdout


def test_unknown_version_fails_cleanly(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [0.1.0] - 2026-01-01\n\n- first\n", encoding="utf-8")

    result = run("v9.9.9", "--changelog", str(changelog))

    assert result.returncode == 1
    assert "no CHANGELOG section for v9.9.9" in result.stderr

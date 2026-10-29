"""Guard: every version the project declares must have a CHANGELOG section.

The release workflow builds the GitHub release body from ``CHANGELOG.md``
(``tools/release_notes.py``), so a tag without a matching section used to fail
only after the wheel was built and the tag pushed. These checks fail in CI
instead, on the commit that bumps the version.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "release_notes.py"
CHANGELOG = ROOT / "CHANGELOG.md"

UNRELEASED = "Unreleased"


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def sections() -> list[str]:
    pattern = r"^## \[([^\]]+)\]"
    return re.findall(pattern, CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)


def test_changelog_has_a_section_for_the_project_version() -> None:
    assert project_version() in sections(), (
        "pyproject.toml has no matching CHANGELOG section; add one before tagging"
    )


def test_the_newest_released_section_is_the_project_version() -> None:
    released = [name for name in sections() if name != UNRELEASED]
    assert released[0] == project_version(), (
        f"newest CHANGELOG release is {released[0]}, project is {project_version()}"
    )


def test_release_notes_are_generated_for_the_project_version() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), project_version()],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"# v{project_version()}")
    assert len(result.stdout.strip().splitlines()) > 3, "the section is effectively empty"

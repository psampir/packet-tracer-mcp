#!/usr/bin/env python3
"""Print one CHANGELOG section as release notes for `gh release --notes-file`."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANGELOG = ROOT / "CHANGELOG.md"


def extract(changelog: str, version: str) -> str:
    """Return ``# vX.Y.Z`` plus the matching CHANGELOG section."""
    v = version.strip().lstrip("v")
    pattern = re.compile(
        rf"^## \[{re.escape(v)}\][^\n]*\n(.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(changelog)
    if not match:
        raise KeyError(v)
    return f"# v{v}\n\n{match.group(1).strip()}\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="tag or version, e.g. v0.3.1 or 0.3.1")
    parser.add_argument(
        "--changelog", default=str(DEFAULT_CHANGELOG), help="path to CHANGELOG.md"
    )
    args = parser.parse_args(argv)

    try:
        text = Path(args.changelog).read_text(encoding="utf-8")
        sys.stdout.write(extract(text, args.version))
    except KeyError:
        print(f"no CHANGELOG section for {args.version}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"cannot read {args.changelog}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

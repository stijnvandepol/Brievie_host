"""Checks that a release is one version everywhere, for the release workflow.

    python3 .github/scripts/release-check.py [--tag vX.Y.Z] [--notes FILE] [--root DIR]

The version lives in backend/pyproject.toml; deploy/release.json, the newest released
entry of CHANGELOG.md and (when given) the git tag must all say the same. On success it
prints `version=X.Y.Z` (for $GITHUB_OUTPUT) and, with --notes, writes that version's
CHANGELOG section to FILE as the release notes. Anything else: one line on stderr and
exit 1. Standard library only; it reads files and writes nothing but FILE.
"""

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

VERSION = re.compile(r"(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})")
HEADING = re.compile(r"^## \[([^\]]+)\](.*)$", re.MULTILINE)
DATED = re.compile(r" - \d{4}-\d{2}-\d{2}")
UNRELEASED = "Onuitgebracht"


class ReleaseCheckError(Exception):
    pass


def project_version(root: Path) -> str:
    data = tomllib.loads((root / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version", "")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ReleaseCheckError(f"backend/pyproject.toml: {version!r} is not a version X.Y.Z")
    return version


RELEASE_KEYS = {"format", "version", "min_upgrade_from", "postgres_major"}


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _version_key(text: object) -> tuple[int, int, int]:
    if not isinstance(text, str) or not VERSION.fullmatch(text):
        raise ReleaseCheckError(f"deploy/release.json: {text!r} is not a version X.Y.Z")
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def check_release_json(root: Path, version: str) -> None:
    """The rules of apps.updates.release.parse_release (held equal by a test): every
    updater must be able to read what this release publishes."""
    try:
        data = json.loads((root / "deploy" / "release.json").read_text(encoding="utf-8"))
    except ValueError:
        raise ReleaseCheckError("deploy/release.json is not JSON") from None
    if not isinstance(data, dict) or not _plain_int(data.get("format")) or data["format"] != 1:
        raise ReleaseCheckError("deploy/release.json: format must be 1")
    if set(data) != RELEASE_KEYS:
        raise ReleaseCheckError(f"deploy/release.json: keys must be {sorted(RELEASE_KEYS)}")
    current = _version_key(data["version"])
    minimum = _version_key(data["min_upgrade_from"])
    major = data["postgres_major"]
    if not _plain_int(major) or not 10 <= major <= 99:
        raise ReleaseCheckError("deploy/release.json: postgres_major must be a number 10-99")
    if minimum > current:
        raise ReleaseCheckError("deploy/release.json: min_upgrade_from is above version")
    if data["version"] != version:
        raise ReleaseCheckError(
            f"deploy/release.json says {data['version']!r}, pyproject says {version!r}"
        )


def changelog_notes(root: Path, version: str) -> str:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    headings = [m for m in HEADING.finditer(text) if m.group(1) != UNRELEASED]
    if not headings:
        raise ReleaseCheckError("CHANGELOG.md has no released version")
    top = headings[0]
    if top.group(1) != version:
        raise ReleaseCheckError(
            f"the newest entry of CHANGELOG.md is {top.group(1)!r}, pyproject says {version!r}"
        )
    if not DATED.fullmatch(top.group(2)):
        raise ReleaseCheckError(f"CHANGELOG.md: the entry {version} has no date (- YYYY-MM-DD)")
    following = HEADING.search(text, top.end())
    notes = text[top.end() : following.start() if following else len(text)].strip()
    if not notes:
        raise ReleaseCheckError(f"CHANGELOG.md: the entry {version} is empty")
    return notes + "\n"


def check(root: Path, tag: str | None) -> tuple[str, str]:
    version = project_version(root)
    check_release_json(root, version)
    notes = changelog_notes(root, version)
    if tag is not None and tag != f"v{version}":
        raise ReleaseCheckError(f"the tag {tag!r} is not v{version}")
    return version, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag")
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    try:
        version, notes = check(args.root, args.tag)
    except (ReleaseCheckError, OSError, ValueError) as error:
        print(f"release-check: {error}", file=sys.stderr)
        return 1
    if args.notes is not None:
        args.notes.write_text(notes, encoding="utf-8")
    print(f"version={version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

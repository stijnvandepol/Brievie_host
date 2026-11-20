"""Makes throwaway releases for the CI job `update` (.github/workflows/ci.yml).

    python3 .github/scripts/update-releases.py SOURCE

SOURCE is a throwaway git clone (never this checkout: the script refuses a directory
without its own .git, and one whose origin is not a local path). On its HEAD, at
version X.Y.Z (backend/pyproject.toml), it tags vX.Y.Z, then commits and tags two
releases on top:

- vX.Y.(Z+1): only the version moves (pyproject, uv.lock, release.json, CHANGELOG);
- vX.Y.(Z+2): the same, plus a migration that fails, so its update fails after the
  switch and the way back has to be taken.

It prints `installed=`, `good=` and `bad=` lines for $GITHUB_OUTPUT. Nothing is pushed.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

VERSION = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)
FAILING_MIGRATION = '''"""A migration that fails on purpose (CI job `update` only)."""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def fail(apps: StateApps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    raise RuntimeError("This release fails its migration on purpose.")


class Migration(migrations.Migration):
    dependencies = [("updates", "0001_initial")]
    operations = [migrations.RunPython(fail, migrations.RunPython.noop)]
'''


def git(source: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", "-C", str(source), *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def set_version(source: Path, old: str, new: str, *, minimum: str) -> None:
    pyproject = source / "backend" / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(f'version = "{old}"', f'version = "{new}"', 1)
    )
    lock = source / "backend" / "uv.lock"
    lock.write_text(
        lock.read_text().replace(
            f'name = "brievie"\nversion = "{old}"', f'name = "brievie"\nversion = "{new}"', 1
        )
    )
    release = source / "deploy" / "release.json"
    data = json.loads(release.read_text())
    data.update(version=new, min_upgrade_from=minimum)
    release.write_text(json.dumps(data) + "\n")
    changelog = source / "CHANGELOG.md"
    text = changelog.read_text()
    first = text.index("\n## [") + 1
    entry = f"## [{new}] - 2026-01-01\n\n### Gewijzigd\n\n- Een proefversie voor CI.\n\n"
    changelog.write_text(text[:first] + entry + text[first:])


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    source = Path(argv[0]).resolve()
    if not (source / ".git").is_dir():
        print("update-releases: SOURCE must be a clone with its own .git", file=sys.stderr)
        return 1
    origin = git(source, "remote", "get-url", "origin")
    if not origin.startswith("/"):
        print("update-releases: SOURCE must be a clone of a local repository", file=sys.stderr)
        return 1
    match = VERSION.search((source / "backend" / "pyproject.toml").read_text())
    if match is None:
        print("update-releases: no version in backend/pyproject.toml", file=sys.stderr)
        return 1
    major, minor, patch = (int(part) for part in match.groups())
    installed = f"{major}.{minor}.{patch}"
    good = f"{major}.{minor}.{patch + 1}"
    bad = f"{major}.{minor}.{patch + 2}"
    git(source, "tag", f"v{installed}")
    set_version(source, installed, good, minimum=installed)
    git(source, "commit", "-q", "-am", f"release {good} (CI)")
    git(source, "tag", f"v{good}")
    set_version(source, good, bad, minimum=good)
    migration = source / "backend" / "apps" / "updates" / "migrations" / "0002_ci_fails.py"
    migration.write_text(FAILING_MIGRATION)
    git(source, "add", str(migration))
    git(source, "commit", "-q", "-am", f"release {bad} (CI, fails)")
    git(source, "tag", f"v{bad}")
    print(f"installed={installed}\ngood={good}\nbad={bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

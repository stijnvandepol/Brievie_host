"""Test doubles for the back-up tests: fake command-line binaries.

Loaded as a pytest plugin (`-p tests.backup_support` in pyproject.toml), so the
fixtures are available everywhere without imports. This module imports no
Django code.

A fake binary is a POSIX `sh` script (macOS and Linux). It appends one JSON
line `{"argv": [...], "env_keys": [...]}` per call to a log file. It records
argument names only, never environment values, and prints nothing itself:
what it answers comes from the table filled with `FakeBinary.respond`.
"""

import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

# Backslash, double quote and tab are escaped; arguments with other control
# characters are not expected in the commands under test.
_SCRIPT = r"""#!/bin/sh
LOG='%(log)s'
TABLE='%(table)s'
json_escape() {
    printf '%%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e "s/$(printf '\t')/\\\\t/g"
}
line='{"argv": ['
sep=''
for a in "$@"; do
    line="$line$sep\"$(json_escape "$a")\""
    sep=', '
done
line="$line], \"env_keys\": ["
sep=''
for k in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p'); do
    line="$line$sep\"$k\""
    sep=', '
done
printf '%%s]}\n' "$line" >> "$LOG"
for f in $(ls -r "$TABLE"/*.prefix 2>/dev/null); do
    base="${f%%.prefix}"
    n=$(wc -w < "$f" | tr -d ' ')
    i=0
    cand=''
    for a in "$@"; do
        [ "$i" -ge "$n" ] && break
        cand="$cand $a"
        i=$((i + 1))
    done
    if [ "$cand" = "$(cat "$f")" ]; then
        cat "$base.out"
        cat "$base.err" >&2
        exit "$(cat "$base.code")"
    fi
done
exit 0
"""


class FakeBinary:
    """A recorded stand-in for a command-line tool.

    `respond` rules are checked newest first; a rule matches when its prefix
    equals the first arguments of the call. Without a match: no output, exit 0.
    """

    def __init__(self, directory: Path, name: str) -> None:
        self.name = name
        self.directory = directory
        self.path = directory / name
        self._log = directory / f"{name}.calls.jsonl"
        self._table = directory / f"{name}.table"
        self._table.mkdir()
        self._log.touch()
        self.path.write_text(_SCRIPT % {"log": self._log, "table": self._table})
        self.path.chmod(self.path.stat().st_mode | stat.S_IXUSR)

    def respond(
        self, prefix: tuple[str, ...], *, stdout: bytes = b"", stderr: str = "", code: int = 0
    ) -> None:
        number = len(list(self._table.glob("*.prefix")))
        base = self._table / f"{number:04d}"
        base.with_suffix(".prefix").write_text("".join(f" {part}" for part in prefix))
        base.with_suffix(".out").write_bytes(stdout)
        base.with_suffix(".err").write_text(stderr)
        base.with_suffix(".code").write_text(str(code))

    def _records(self) -> list[dict[str, list[str]]]:
        lines = self._log.read_text().splitlines()
        records: list[dict[str, list[str]]] = [json.loads(line) for line in lines]
        return records

    @property
    def calls(self) -> list[list[str]]:
        return [record["argv"] for record in self._records()]

    @property
    def env_keys(self) -> list[list[str]]:
        return [record["env_keys"] for record in self._records()]


FakeRestic = FakeBinary


@pytest.fixture
def fake_restic(tmp_path: Path, settings: object) -> FakeBinary:
    directory = tmp_path / "fake-restic"
    directory.mkdir()
    fake = FakeBinary(directory, "restic")
    settings.BRIEVIE_RESTIC_BINARY = str(fake.path)  # type: ignore[attr-defined]
    return fake


@pytest.fixture
def fake_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], FakeBinary]:
    """Factory: `fake_tool("pg_dump")` puts a fake `pg_dump` first on PATH."""
    directory = tmp_path / "fake-tools"
    directory.mkdir()
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ.get('PATH', '')}")

    def make(name: str) -> FakeBinary:
        return FakeBinary(directory, name)

    return make


@pytest.fixture
def configured_folder(tmp_path: Path, settings: object) -> Path:
    """A folder destination that `initialise` already prepared, plus private config dirs.

    Returns the mount. The back-up password is a real (non-placeholder) value.
    """
    # Imported here: this module imports no Django code at import time.
    from apps.backup import destination

    mount = tmp_path / "backup-mount"
    (mount / destination.REPOSITORY_DIR).mkdir(parents=True)
    marker = {"format": destination.MARKER_FORMAT, "repository": destination.REPOSITORY_DIR}
    (mount / destination.MARKER_NAME).write_text(json.dumps(marker))
    values = {
        "BRIEVIE_BACKUP_DIR_SET": True,
        "BRIEVIE_BACKUP_MOUNT": str(mount),
        "BRIEVIE_BACKUP_REPOSITORY": "",
        "BRIEVIE_BACKUP_PASSWORD": "p" * 40,
        "BRIEVIE_BACKUP_CONFIG_DIR": str(tmp_path / "backup-config"),
        "BRIEVIE_AUTHORITY_COPY_DIR": str(tmp_path / "authority"),
    }
    for name, value in values.items():
        setattr(settings, name, value)
    return mount

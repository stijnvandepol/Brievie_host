"""Test doubles for the back-up tests: fake command-line binaries.

Loaded as a pytest plugin (`-p tests.backup_support` in pyproject.toml), so the
fixtures are available everywhere without imports. This module imports no
Django code.

A fake binary is a POSIX `sh` script (macOS and Linux). It appends one JSON
line `{"argv": [...], "env_keys": [...]}` per call to a log file. It records
argument names only, never environment values, and prints nothing itself:
what it answers comes from the table filled with `FakeBinary.respond`.
"""

import builtins
import io
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterator
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
def owner_conninfo() -> str:
    """libpq conninfo for the test database as the owner role (it owns backup_backuprun)."""
    # Imported here: this module imports no Django code at import time.
    from django.conf import settings
    from django.db import connection
    from psycopg.conninfo import make_conninfo

    owner = settings.DATABASE_OWNER
    if owner is None:
        pytest.fail("The test run needs DATABASE_OWNER_URL (see deploy/.env.example).")
    return make_conninfo(
        dbname=str(connection.settings_dict["NAME"]),
        user=str(owner["USER"]),
        password=str(owner["PASSWORD"]),
        host=str(owner["HOST"]),
        port=str(owner["PORT"]),
    )


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


# --- the recorder: every destructive primitive fails the test -------------------------

# (owner, name) of every primitive that deletes, moves, creates, overwrites, changes
# the owner or mode of a file, or starts a process. `os.open` and `open` are replaced
# by versions that refuse only a write, create or truncate.
_DESTRUCTIVE: tuple[tuple[object, str], ...] = (
    *(
        (os, name)
        for name in (
            "scandir",
            "unlink",
            "remove",
            "rmdir",
            "removedirs",
            "replace",
            "rename",
            "renames",
            "chown",
            "lchown",
            "fchown",
            "chmod",
            "fchmod",
            "truncate",
            "ftruncate",
            "symlink",
            "link",
            "mkdir",
            "makedirs",
            "mkfifo",
            "mknod",
            "utime",
            "system",
            "posix_spawn",
            "posix_spawnp",
            "fork",
            "execv",
            "execve",
            "execvp",
        )
        if hasattr(os, name)
    ),
    *((os, name) for name in ("lchmod", "chflags", "lchflags") if hasattr(os, name)),
    *(
        (shutil, name)
        for name in (
            "rmtree",
            "move",
            "copy",
            "copy2",
            "copyfile",
            "copytree",
            "copymode",
            "copystat",
            "chown",
        )
    ),
    *((tempfile, name) for name in ("mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryFile")),
    *(
        (Path, name)
        for name in (
            "unlink",
            "rmdir",
            "mkdir",
            "chmod",
            "lchmod",
            "write_text",
            "write_bytes",
            "touch",
            "rename",
            "replace",
            "symlink_to",
            "hardlink_to",
        )
    ),
    *((subprocess, name) for name in ("Popen", "run", "call", "check_call", "check_output")),
)
_PROCESS_PRIMITIVES = ("posix_spawn", "posix_spawnp", "fork", "execv", "execve", "execvp")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_WRITE_MODES = frozenset("wax+")


class Recorder:
    """Replaces every destructive primitive by one that records the call and fails.

    Arm it right before the call under test (the test's own set-up under tmp_path may
    still create files); `disarm` restores everything. Reading stays possible: `stat`,
    `realpath`, `os.open` and `open` for reading.
    """

    def __init__(self) -> None:
        self.called: list[str] = []
        self._patch = pytest.MonkeyPatch()

    def _refuse(self, name: str) -> Callable[..., None]:
        def refuse(*_args: object, **_kwargs: object) -> None:
            self.called.append(name)
            raise AssertionError(f"destructive primitive called: {name}")

        return refuse

    def arm(self, *, allow: tuple[str, ...] = ()) -> None:
        tempfile.gettempdir()  # computed (and cached) before anything is replaced
        if "Popen" in allow or "run" in allow:
            # How subprocess starts a process differs per platform (Linux: posix_spawn,
            # macOS: fork and exec); allowing a start allows all of its ways.
            allow = (*allow, *_PROCESS_PRIMITIVES)
        for owner, name in _DESTRUCTIVE:
            if name not in allow:
                self._patch.setattr(owner, name, self._refuse(name))
        real_os_open = os.open
        real_open = builtins.open

        def os_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
            if flags & _WRITE_FLAGS:
                self.called.append("os.open for writing")
                raise AssertionError("destructive primitive called: os.open for writing")
            return real_os_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

        def open_(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
            if _WRITE_MODES & set(mode):
                self.called.append("open for writing")
                raise AssertionError("destructive primitive called: open for writing")
            return real_open(file, mode, *args, **kwargs)  # type: ignore[call-overload]

        self._patch.setattr(os, "open", os_open)
        self._patch.setattr(builtins, "open", open_)
        self._patch.setattr(io, "open", open_)

    def disarm(self) -> None:
        self._patch.undo()


@pytest.fixture
def recorder() -> Iterator[Recorder]:
    """A Recorder that is disarmed after the test, which fails if anything was called."""
    armed = Recorder()
    yield armed
    armed.disarm()
    assert armed.called == []

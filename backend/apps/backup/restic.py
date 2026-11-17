"""Build restic command lines and environments, and run the binary.

Every argv builder is pure and returns the arguments *after* the binary.
Secrets (the repository password, storage credentials, libpq passwords) travel
only in the environment given to `run`, never in argv, and restic's stderr is
redacted before anyone sees it.
"""

import json
import os
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Literal
from uuid import UUID

from django.conf import settings

from apps.backup.errors import BackupError

HOST = "brievie"
DB_FILENAME = "brievie.pgdump"
TMP_EXCLUDE = ".tmp-*"
RETENTION = (
    "--keep-within-daily",
    "7d",
    "--keep-within-weekly",
    "1m",
    "--keep-within-monthly",
    "1y",
)
CHECK_SUBSET = "5%"
SUPPORTED_SCHEMES = ("rest:", "s3:")

_USERINFO_IN_REPOSITORY = re.compile(r"^[a-z0-9+.-]+://[^/@]*@", re.IGNORECASE)
_USERINFO_IN_TEXT = re.compile(r"([a-z][a-z0-9+.-]*)://[^/\s@]*@", re.IGNORECASE)
_MAX_STDERR = 2000
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class Repository:
    kind: Literal["folder", "rest", "s3"]
    location: str  # in restic notation
    credentials: Mapping[str, str]  # already under restic's own variable names


@dataclass(frozen=True)
class ResticResult:
    returncode: int
    stdout: bytes
    stderr: str  # already redacted


@dataclass(frozen=True)
class Snapshot:
    id: str
    time: datetime
    tags: tuple[str, ...]
    paths: tuple[str, ...]


def check_repository_value(value: str) -> None:
    """Refuse an unsupported scheme, or a repository address that carries credentials."""
    scheme = next((s for s in SUPPORTED_SCHEMES if value.startswith(s)), None)
    if scheme is None:
        raise BackupError("repository_unsupported")
    if _USERINFO_IN_REPOSITORY.match(value[len(scheme) :]):
        raise BackupError("repository_has_credentials")


def tags(run_id: UUID, kind: Literal["db", "files"]) -> list[str]:
    return ["--tag", f"run:{run_id}", "--tag", f"kind:{kind}"]


def init_argv() -> list[str]:
    return ["init"]


def unlock_argv() -> list[str]:
    """Removes stale locks only; never `--remove-all`."""
    return ["unlock"]


def backup_stdin_argv(run_id: UUID, command: Sequence[str]) -> list[str]:
    return [
        "backup",
        "--json",
        "--host",
        HOST,
        *tags(run_id, "db"),
        "--stdin-from-command",
        "--stdin-filename",
        DB_FILENAME,
        "--",
        *command,
    ]


def backup_files_argv(run_id: UUID, paths: Sequence[str]) -> list[str]:
    return [
        "backup",
        "--json",
        "--host",
        HOST,
        *tags(run_id, "files"),
        "--exclude",
        TMP_EXCLUDE,
        *paths,
    ]


def forget_argv() -> list[str]:
    # Never group by tags: every run has its own `run:` tag, so nothing would be pruned.
    return ["forget", "--prune", "--host", HOST, "--group-by", "host,paths", *RETENTION]


def check_argv() -> list[str]:
    return ["check", "--read-data-subset", CHECK_SUBSET]


def snapshots_argv(tag: str) -> list[str]:
    return ["snapshots", "--json", "--tag", tag]


def dump_argv(snapshot_id: str, path: str) -> list[str]:
    return ["dump", snapshot_id, path]


def restore_argv(snapshot_id: str, target: str, includes: Sequence[str]) -> list[str]:
    argv = ["restore", snapshot_id, "--target", target]
    for include in includes:
        argv += ["--include", include]
    return argv


def restic_env(
    repository: Repository, password: str, *, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The whole environment of a restic run; nothing else is taken from `os.environ`."""
    return {
        "PATH": os.environ.get("PATH", _DEFAULT_PATH),
        "HOME": os.environ.get("HOME", "/tmp"),  # noqa: S108
        "RESTIC_REPOSITORY": repository.location,
        "RESTIC_PASSWORD": password,
        **repository.credentials,
        **(extra or {}),
    }


def redact(text: str, secrets: Iterable[str]) -> str:
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "***")
    text = _USERINFO_IN_TEXT.sub(r"\1://***@", text)
    return text[:_MAX_STDERR]


def run(
    argv: Sequence[str],
    env: Mapping[str, str],
    *,
    secrets: Sequence[str],
    stdin: int | None = None,
    stdout: int | IO[bytes] = subprocess.PIPE,
    timeout: float | None = None,
) -> ResticResult:
    try:
        done = subprocess.run(  # noqa: S603
            [settings.BRIEVIE_RESTIC_BINARY, *argv],
            env=dict(env),
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        # Neither the binary's path nor its arguments belong in a message.
        raise BackupError("restic_failed") from error
    return ResticResult(
        returncode=done.returncode,
        stdout=done.stdout or b"",
        stderr=redact(done.stderr.decode(errors="replace"), secrets),
    )


_EXIT_CODES = {10: "repository_missing", 11: "repository_locked", 12: "wrong_password"}
_UNREACHABLE = ("connection refused", "no such host", "i/o timeout")


def classify(result: ResticResult) -> str:
    """Map a restic result (exit codes of restic >= 0.17) to a back-up error code."""
    if result.returncode in _EXIT_CODES:
        return _EXIT_CODES[result.returncode]
    if result.returncode == 3:
        return "files_incomplete"
    if result.returncode == 1:
        text = result.stderr.lower()
        if "no space left on device" in text:
            return "destination_full"
        if any(marker in text for marker in _UNREACHABLE):
            return "destination_unreachable"
    return "restic_failed"


def raise_for(result: ResticResult) -> None:
    if result.returncode != 0:
        raise BackupError(classify(result))


def summary_bytes(stdout: bytes) -> int:
    """`total_bytes_processed` of the last `summary` line of `restic backup --json`."""
    total: int | None = None
    for line in stdout.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("message_type") == "summary":
            value = record.get("total_bytes_processed")
            if isinstance(value, int):
                total = value
    if total is None:
        raise BackupError("restic_failed")
    return total


def parse_snapshots(stdout: bytes) -> list[Snapshot]:
    """Snapshots of `restic snapshots --json`, newest first."""
    records = json.loads(stdout) or []
    snapshots = [
        Snapshot(
            id=str(record["id"]),
            time=datetime.fromisoformat(str(record["time"])),
            tags=tuple(record.get("tags") or ()),
            paths=tuple(record.get("paths") or ()),
        )
        for record in records
    ]
    return sorted(snapshots, key=lambda snapshot: snapshot.time, reverse=True)

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
from datetime import UTC, datetime
from typing import IO, Literal
from uuid import UUID

from django.conf import settings

from apps.backup.errors import BackupError

HOST = "brievie"
# The tag of a restore's safety back-up, and how long retention keeps those.
SAFETY_TAG = "kind:safety"
SAFETY_KEEP = "30d"
DB_FILENAME = "brievie.pgdump"
TMP_EXCLUDE = ".tmp-*"
# Every snapshot of the last two days (counted from the newest) stays; older ones
# thin out to one a day for a week, one a week for a month, one a month for a year.
# The daily rule alone keeps one snapshot per day, so a second back-up the same day
# removed the first: the safety back-up of a restore pruned the run being restored.
RETENTION = (
    "--keep-within",
    "2d",
    "--keep-within-daily",
    "7d",
    "--keep-within-weekly",
    "1m",
    "--keep-within-monthly",
    "1y",
)
CHECK_SUBSET = "5%"
SUPPORTED_SCHEMES = ("rest:", "s3:")

_NESTED_SCHEME = re.compile(r"^[a-z0-9+.-]+://", re.IGNORECASE)
# Greedy: a password may contain "@", so the userinfo ends at the last "@" of the authority.
_USERINFO_IN_TEXT = re.compile(r"([a-z][a-z0-9+.-]*)://[^/\s]*@", re.IGNORECASE)
_USERINFO_AFTER_PREFIX = re.compile(r"\b(rest:|s3:)[^/\s]*@", re.IGNORECASE)
# restic prints a snapshot id as 64 hex characters, the short form as 8.
_SNAPSHOT_ID = re.compile(r"[0-9a-f]{8,64}")
_GLOB = re.compile(r"[*?[\\]")
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
    rest = _NESTED_SCHEME.sub("", value[len(scheme) :])
    if "@" in rest.split("/", 1)[0]:
        raise BackupError("repository_has_credentials")


def tags(run_id: UUID, kind: Literal["db", "files"]) -> list[str]:
    return ["--tag", f"run:{run_id}", "--tag", f"kind:{kind}"]


def _run_marks(at: datetime | None, safety: bool) -> list[str]:
    """The safety tag, and the run's own time (UTC; restic reads --time in TZ=UTC)."""
    marks = ["--tag", SAFETY_TAG] if safety else []
    if at is not None:
        marks += ["--time", at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")]
    return marks


def init_argv() -> list[str]:
    return ["init"]


def _lock(no_lock: bool) -> list[str]:
    """`--no-lock` for a read on a repository that may be read-only (a restore source).

    Never for a back-up, forget, check or unlock: those need the repository's lock.
    """
    return ["--no-lock"] if no_lock else []


def cat_config_argv(*, no_lock: bool = False) -> list[str]:
    """Opens the repository's config: proves the password fits an existing repository."""
    return ["cat", *_lock(no_lock), "config"]


def unlock_argv() -> list[str]:
    """Removes stale locks only; never `--remove-all`."""
    return ["unlock"]


def backup_stdin_argv(
    run_id: UUID,
    command: Sequence[str],
    *,
    host: str = HOST,
    at: datetime | None = None,
    safety: bool = False,
) -> list[str]:
    return [
        "backup",
        "--json",
        "--host",
        host,
        *tags(run_id, "db"),
        *_run_marks(at, safety),
        "--stdin-from-command",
        "--stdin-filename",
        DB_FILENAME,
        "--",
        *command,
    ]


def backup_files_argv(
    run_id: UUID,
    paths: Sequence[str],
    *,
    host: str = HOST,
    at: datetime | None = None,
    safety: bool = False,
    excludes: Sequence[str] = (),
) -> list[str]:
    return [
        "backup",
        "--json",
        "--host",
        host,
        *tags(run_id, "files"),
        *_run_marks(at, safety),
        "--exclude",
        TMP_EXCLUDE,
        *(argument for path in excludes for argument in ("--exclude", path)),
        "--",
        *paths,
    ]


def forget_argv(host: str = HOST) -> list[str]:
    """Retention of this installation's own snapshots; safety snapshots are kept.

    Never group by tags: every run has its own `run:` tag, so nothing would be pruned.
    """
    return [
        "forget",
        "--prune",
        "--host",
        host,
        "--group-by",
        "host,paths",
        "--keep-tag",
        SAFETY_TAG,
        *RETENTION,
    ]


def forget_safety_argv(host: str = HOST) -> list[str]:
    """Safety snapshots of restores, kept for 30 days (the next retention prunes)."""
    return [
        "forget",
        "--host",
        host,
        "--tag",
        SAFETY_TAG,
        "--group-by",
        "host,paths",
        "--keep-within",
        SAFETY_KEEP,
    ]


def snapshots_host_argv(host: str) -> list[str]:
    """Every snapshot of one host (for retention: its clock and its half runs)."""
    return ["snapshots", "--json", "--host", host]


def forget_run_argv(run_id: UUID, host: str = HOST) -> list[str]:
    """Removes every snapshot of one run (no prune: the next retention run prunes).

    restic refuses `forget --tag` without a policy, and --unsafe-allow-remove-all
    removes all snapshots that match the filters; the host and the run's own tag are
    therefore always both present.
    """
    return ["forget", "--unsafe-allow-remove-all", "--host", host, "--tag", f"run:{run_id}"]


def check_argv() -> list[str]:
    return ["check", "--read-data-subset", CHECK_SUBSET]


def snapshots_argv(tag: str, *, no_lock: bool = False) -> list[str]:
    return ["snapshots", *_lock(no_lock), "--json", "--tag", tag]


def snapshot_id_value(snapshot_id: str) -> str:
    """A snapshot id as restic prints it: 8 to 64 lowercase hex characters, or refused."""
    if _SNAPSHOT_ID.fullmatch(snapshot_id) is None:
        raise BackupError("restore_failed")
    return snapshot_id


def dump_argv(snapshot_id: str, path: str, *, no_lock: bool = False) -> list[str]:
    # `--` ends the options: neither the id nor the path is ever read as one.
    return ["dump", *_lock(no_lock), "--", snapshot_id_value(snapshot_id), path]


def restore_argv(
    snapshot_id: str, target: str, includes: Sequence[str], *, no_lock: bool = False
) -> list[str]:
    """`restore` of `includes` (absolute paths without glob characters) to `target`."""
    # Extended attributes and ACLs from a back-up are never restored.
    argv = ["restore", *_lock(no_lock), "--target", target, "--exclude-xattr", "*"]
    for include in includes:
        if not include.startswith("/") or _GLOB.search(include):
            raise BackupError("unsafe_path")
        argv += ["--include", include]
    return [*argv, "--", snapshot_id_value(snapshot_id)]


def restic_env(
    repository: Repository, password: str, *, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The whole environment of a restic run; nothing else is taken from `os.environ`."""
    return {
        "PATH": os.environ.get("PATH", _DEFAULT_PATH),
        "HOME": os.environ.get("HOME", "/tmp"),  # noqa: S108
        "RESTIC_REPOSITORY": repository.location,
        "RESTIC_PASSWORD": password,
        # --time and the snapshot times are UTC, whatever the container's zone.
        "TZ": "UTC",
        **repository.credentials,
        **(extra or {}),
    }


def redact(text: str, secrets: Iterable[str]) -> str:
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "***")
    text = _USERINFO_IN_TEXT.sub(r"\1://***@", text)
    text = _USERINFO_AFTER_PREFIX.sub(r"\1***@", text)
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
    try:
        records = json.loads(stdout) or []
        if not isinstance(records, list):
            raise BackupError("restic_failed")
        snapshots = [
            Snapshot(
                id=str(record["id"]),
                time=datetime.fromisoformat(str(record["time"])),
                tags=tuple(record.get("tags") or ()),
                paths=tuple(record.get("paths") or ()),
            )
            for record in records
        ]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise BackupError("restic_failed") from error
    return sorted(snapshots, key=lambda snapshot: snapshot.time, reverse=True)

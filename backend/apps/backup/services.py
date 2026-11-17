import logging
import os
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.backup import destination, jobs, restic
from apps.backup.database import (
    backup_conninfo,
    libpq_env,
    require_backup_role,
    require_client_for,
)
from apps.backup.destination import backup_password, configured_repository, destination_kind
from apps.backup.errors import REASONS, BackupError
from apps.backup.locks import backup_lock
from apps.backup.manifest import MANIFEST_NAME, brievie_version, build_manifest, manifest_to_json
from apps.backup.models import OPEN_STATUSES, BackupRun
from apps.backup.privatefile import write_private

logger = logging.getLogger(__name__)

# The uid the service runs as; the one-off container that initialises runs as root.
KEEPER_UID = 10001

# Run by restic (`--stdin-from-command`); database, user and password come from the
# environment (libpq_env). Never `--enable-row-security`: that dumps only visible rows.
DUMP_COMMAND = ("pg_dump", "--format=custom", "--no-password")

# A request takes this transaction lock, so two requests never make two open runs.
_REQUEST_LOCK_KEY = int.from_bytes(b"brvbkreq", "big")
_STDERR_TAIL = 500
_VERSION_TIMEOUT = 30
_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
_PG_DUMP_VERSION = re.compile(r"\(PostgreSQL\)\s+(\S+)")
_RESTIC_VERSION = re.compile(r"^restic\s+(\S+)")


def initialise_destination(*, allow_same_disk: bool) -> str:
    """Prepare the configured destination for the first time; returns its kind."""
    owner_uid = KEEPER_UID if os.geteuid() == 0 else None
    with backup_lock():
        destination.initialise(allow_same_disk=allow_same_disk, owner_uid=owner_uid)
    return destination.destination_kind()


def file_paths() -> list[str]:
    """What the files snapshot holds: the documents, the authority copy and the config dir."""
    return [
        settings.BRIEVIE_STORAGE_ROOT,
        settings.BRIEVIE_AUTHORITY_COPY_DIR,
        settings.BRIEVIE_BACKUP_CONFIG_DIR,
    ]


def _kind_or_blank() -> str:
    try:
        return destination_kind()
    except BackupError:
        # The run fails with the reason when it starts; a request never refuses.
        return ""


@transaction.atomic
def request_run(kind: str, *, requested_by: str = "cli") -> BackupRun:
    """The open run of this kind, or a new queued one with its job deferred."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_REQUEST_LOCK_KEY])
    existing = BackupRun.objects.filter(kind=kind, status__in=OPEN_STATUSES).first()
    if existing is not None:
        return existing
    run = BackupRun.objects.create(
        kind=kind, requested_by=requested_by, destination_kind=_kind_or_blank()
    )
    jobs.run_backup.defer(run_id=str(run.id))
    return run


@transaction.atomic
def _mark_running(run_id: UUID) -> None:
    BackupRun.objects.filter(id=run_id, status__in=OPEN_STATUSES).update(
        status=BackupRun.Status.RUNNING, started_at=timezone.now(), updated_at=timezone.now()
    )


@transaction.atomic
def _finish(run_id: UUID, status: str, **fields: object) -> None:
    # Only an open run: a restore may have closed it meanwhile, and that stands.
    now = timezone.now()
    BackupRun.objects.filter(id=run_id, status__in=OPEN_STATUSES).update(
        status=status, finished_at=now, updated_at=now, **fields
    )


def _location_secrets(repository: restic.Repository) -> list[str]:
    """The repository address and its host: they never reach a log or a reason."""
    values = [repository.location]
    if repository.kind != "folder":
        inner = repository.location.split(":", 1)[1]
        split = urlsplit(inner if "://" in inner else f"//{inner}")
        values += [inner, split.netloc]
        if split.hostname and len(split.hostname) >= 3:
            values.append(split.hostname)
    return [value for value in values if value]


class _Restic:
    """Runs restic for one run; puts the redacted stderr tail of a failure in `failures`."""

    def __init__(self, repository: restic.Repository, password: str, failures: list[str]) -> None:
        self.repository = repository
        self.password = password
        self.secrets = [*destination.secrets(), *_location_secrets(repository)]
        self.failures = failures

    def __call__(self, argv: Sequence[str], *, extra: dict[str, str] | None = None) -> bytes:
        # No timeout: killing restic during the dump would orphan the pg_dump it started.
        env = restic.restic_env(self.repository, self.password, extra=extra)
        result = restic.run(argv, env, secrets=self.secrets)
        if result.returncode != 0:
            self.failures.append(result.stderr[-_STDERR_TAIL:])
            restic.raise_for(result)
        return result.stdout


def _database_conninfo() -> str:
    # The database the app uses, read as the back-up role.
    return backup_conninfo(dbname=str(connection.settings_dict["NAME"]))


def _back_up(run: BackupRun, failures: list[str]) -> None:
    """Steps 3-10 of a run, under the back-up lock (D 4.3: database first, then files)."""
    with backup_lock():
        repository = configured_repository()
        run_restic = _Restic(repository, backup_password(), failures)
        run_restic(restic.unlock_argv())
        if run.kind == BackupRun.Kind.CHECK:
            run_restic(restic.check_argv())
            _finish(run.id, BackupRun.Status.SUCCEEDED)
            return

        conninfo = _database_conninfo()
        require_backup_role(conninfo)
        require_client_for(conninfo)
        dump_out = run_restic(
            restic.backup_stdin_argv(run.id, DUMP_COMMAND), extra=libpq_env(conninfo)
        )
        size = restic.summary_bytes(dump_out)

        manifest = build_manifest(
            run_id=run.id,
            conninfo=conninfo,
            storage_root=Path(settings.BRIEVIE_STORAGE_ROOT),
            authority_dir=Path(settings.BRIEVIE_AUTHORITY_COPY_DIR),
        )
        write_private(
            Path(settings.BRIEVIE_BACKUP_CONFIG_DIR), MANIFEST_NAME, manifest_to_json(manifest)
        )

        files_out = run_restic(restic.backup_files_argv(run.id, file_paths()))
        size += restic.summary_bytes(files_out)
        if not settings.BRIEVIE_BACKUP_APPEND_ONLY:
            run_restic(restic.forget_argv())
        _finish(run.id, BackupRun.Status.SUCCEEDED, size_bytes=size)


def perform_run(run_id: UUID) -> None:
    """Make or check a back-up; the job's whole body. Never raises: no automatic retry.

    Not one transaction: it runs external programs for hours. Each status change is
    its own short transaction.
    """
    run = BackupRun.objects.filter(id=run_id).first()
    if run is None or run.status not in OPEN_STATUSES:
        return
    _mark_running(run_id)
    failures: list[str] = []
    try:
        _back_up(run, failures)
    except BackupError as error:
        _finish(run_id, BackupRun.Status.FAILED, reason_code=error.code, reason=error.message)
        logger.warning("Backup run %s failed: %s", run_id, error.code)
        for stderr in failures:
            # Never stdout: `backup --json` may carry messages there.
            logger.warning("Backup run %s restic stderr: %s", run_id, stderr)
    except Exception as error:  # noqa: BLE001 - every failure must end in a visible status
        _finish(
            run_id,
            BackupRun.Status.FAILED,
            reason_code="internal_error",
            reason=str(REASONS["internal_error"]),
        )
        # The type only: a message can quote a path or a connection string.
        logger.error("Backup run %s failed with an unexpected %s", run_id, type(error).__name__)


def wait_for_run(
    run_id: UUID,
    *,
    timeout: float,
    pickup_timeout: float = 120.0,
    poll: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> BackupRun:
    """The run once it is finished.

    Raises TimeoutError when it is still queued after `pickup_timeout` (nobody runs
    the queue) or still open after `timeout`.
    """
    start = clock()
    while True:
        run = BackupRun.objects.get(id=run_id)
        if run.status not in OPEN_STATUSES:
            return run
        limit = pickup_timeout if run.status == BackupRun.Status.QUEUED else timeout
        remaining = min(limit, timeout) - (clock() - start)
        if remaining <= 0:
            raise TimeoutError
        sleep(min(poll, remaining))


@dataclass(frozen=True)
class BackupStatus:
    brievie_version: str
    postgres_version: str
    pg_dump_version: str
    restic_version: str
    destination_kind: str
    last_success: BackupRun | None
    last_check: BackupRun | None
    last_failure: BackupRun | None
    open_run: BackupRun | None


def _tool_version(argv: list[str], pattern: re.Pattern[str]) -> str:
    """The version a binary prints, or "?" when it is missing or says something else."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no secret
            argv,
            capture_output=True,
            text=True,
            check=True,
            timeout=_VERSION_TIMEOUT,
            env={"PATH": os.environ.get("PATH", _DEFAULT_PATH)},
        )
    except (OSError, subprocess.SubprocessError):
        return "?"
    match = pattern.search(result.stdout)
    return match.group(1) if match else "?"


def _postgres_version() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            row = cursor.fetchone()
    except DatabaseError:
        return "?"
    return str(row[0]).split()[0] if row else "?"


def _destination_kind_for_status() -> str:
    try:
        return destination_kind()
    except BackupError:
        return "?"


def status_summary() -> BackupStatus:
    runs = BackupRun.objects.order_by("-created_at")
    return BackupStatus(
        brievie_version=brievie_version(),
        postgres_version=_postgres_version(),
        pg_dump_version=_tool_version(["pg_dump", "--version"], _PG_DUMP_VERSION),
        restic_version=_tool_version([settings.BRIEVIE_RESTIC_BINARY, "version"], _RESTIC_VERSION),
        destination_kind=_destination_kind_for_status(),
        last_success=runs.filter(
            kind=BackupRun.Kind.BACKUP, status=BackupRun.Status.SUCCEEDED
        ).first(),
        last_check=runs.filter(
            kind=BackupRun.Kind.CHECK, status=BackupRun.Status.SUCCEEDED
        ).first(),
        last_failure=runs.filter(status=BackupRun.Status.FAILED).first(),
        open_run=runs.filter(status__in=OPEN_STATUSES).first(),
    )

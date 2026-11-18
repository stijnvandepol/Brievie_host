import logging
import os
import re
import stat
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.backup import destination, jobs, restic, safepaths
from apps.backup.database import (
    backup_conninfo,
    libpq_env,
    require_backup_role,
    require_client_for,
)
from apps.backup.destination import backup_password, configured_repository, destination_kind
from apps.backup.envcopy import ENV_COPY_NAME
from apps.backup.errors import REASONS, BackupError
from apps.backup.locks import backup_lock
from apps.backup.manifest import (
    MANIFEST_NAME,
    brievie_version,
    build_manifest,
    counting_transaction,
    export_snapshot,
    manifest_to_json,
)
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
# For restic calls that start no child and should be quick: unlock and the clean-up.
SHORT_RESTIC_TIMEOUT = 600
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


INSTALLATION_ID_NAME = "installation-id"
_INSTALLATION_ID = re.compile(r"[0-9a-f]{32}")
_INSTALLATION_ID_LIMIT = 128


def installation_id() -> str | None:
    """This installation's id from the config folder (./brievie writes it), when the
    file is a regular file (never read through a link) holding 32 lowercase hex."""
    config = safepaths.configured_path(settings.BRIEVIE_BACKUP_CONFIG_DIR)
    safepaths.require_emptiable(config, allowed=[config], fixed=[safepaths.BACKUP_CONFIG])
    try:
        descriptor = safepaths.open_dir_nofollow(config)
    except (FileNotFoundError, BackupError):
        return None
    try:
        data = safepaths.read_regular_at(
            descriptor, INSTALLATION_ID_NAME, limit=_INSTALLATION_ID_LIMIT
        )
    except (BackupError, OSError):
        return None
    finally:
        os.close(descriptor)
    if data is None:
        return None
    text = data.decode("ascii", errors="replace").strip()
    return text if _INSTALLATION_ID.fullmatch(text) else None


def backup_host() -> str:
    """The restic host of this installation's back-ups and retention:
    `brievie-<first 12 hex of the installation id>`, else the shared `brievie`."""
    found = installation_id()
    return f"{restic.HOST}-{found[:12]}" if found else restic.HOST


def file_excludes() -> list[str]:
    """Never in a back-up: the installation id (and its temporary file)."""
    config = str(settings.BRIEVIE_BACKUP_CONFIG_DIR).rstrip("/")
    return [f"{config}/{INSTALLATION_ID_NAME}", f"{config}/{INSTALLATION_ID_NAME}.tmp"]


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


def _has_live_job(run_id: UUID) -> bool:
    return ProcrastinateJob.objects.filter(
        task_name="run_backup", status__in=["todo", "doing"], args__run_id=str(run_id)
    ).exists()


@transaction.atomic
def request_run(kind: str, *, requested_by: str = "cli") -> BackupRun:
    """The open run of this kind, or a new queued one with its job deferred.

    An open run without a waiting or running job was abandoned (its job was
    cancelled, or recording its end failed): it is closed, so it never blocks, and
    the new run's job removes the snapshots it may have left (its id travels in the
    job's arguments, together with those its own job still had to remove).
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_REQUEST_LOCK_KEY])
    clean_up: list[str] = []
    for existing in BackupRun.objects.filter(kind=kind, status__in=OPEN_STATUSES):
        if _has_live_job(existing.id):
            return existing
        logger.warning("Backup run %s has no job left; closed as abandoned", existing.id)
        _finish(
            existing.id,
            BackupRun.Status.INTERRUPTED,
            reason_code="abandoned",
            reason=str(REASONS["abandoned"]),
        )
        if existing.kind == BackupRun.Kind.BACKUP:
            clean_up += [str(existing.id), *_pending_clean_up(existing.id)]
    run = BackupRun.objects.create(
        kind=kind, requested_by=requested_by, destination_kind=_kind_or_blank()
    )
    if clean_up:
        jobs.run_backup.defer(run_id=str(run.id), clean_up_run_ids=clean_up)
    else:
        jobs.run_backup.defer(run_id=str(run.id))
    return run


def _pending_clean_up(run_id: UUID) -> list[str]:
    """The clean-up the jobs of an abandoned run were given and may not have done."""
    pending: list[str] = []
    for args in ProcrastinateJob.objects.filter(
        task_name="run_backup", args__run_id=str(run_id)
    ).values_list("args", flat=True):
        ids = args.get("clean_up_run_ids") if isinstance(args, dict) else None
        if isinstance(ids, list):
            pending += [str(i) for i in ids]
    return pending


@transaction.atomic
def _mark_running(run_id: UUID) -> None:
    # A resumed run (retried stalled job) keeps the moment it first started.
    now = timezone.now()
    BackupRun.objects.filter(id=run_id, status__in=OPEN_STATUSES).update(
        status=BackupRun.Status.RUNNING,
        started_at=Coalesce(F("started_at"), Value(now)),
        updated_at=now,
    )


@transaction.atomic
def _finish(run_id: UUID, status: str, **fields: object) -> None:
    # Only an open run: a restore may have closed it meanwhile, and that stands.
    now = timezone.now()
    BackupRun.objects.filter(id=run_id, status__in=OPEN_STATUSES).update(
        status=status, finished_at=now, updated_at=now, **fields
    )


def location_secrets(repository: restic.Repository) -> list[str]:
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

    def __init__(
        self,
        repository: restic.Repository,
        password: str,
        failures: list[str],
        host: str = restic.HOST,
    ) -> None:
        self.repository = repository
        self.host = host
        self.password = password
        self.secrets = [*destination.secrets(), *location_secrets(repository)]
        self.failures = failures

    def result(
        self,
        argv: Sequence[str],
        *,
        extra: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> restic.ResticResult:
        """Run without raising on an exit code; a failure's stderr tail is kept."""
        env = restic.restic_env(self.repository, self.password, extra=extra)
        result = restic.run(argv, env, secrets=self.secrets, timeout=timeout)
        if result.returncode != 0:
            self.failures.append(result.stderr[-_STDERR_TAIL:])
        return result

    def __call__(
        self,
        argv: Sequence[str],
        *,
        extra: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> bytes:
        result = self.result(argv, extra=extra, timeout=timeout)
        restic.raise_for(result)
        return result.stdout

    def forget_run_unless_whole(self, run_id: UUID) -> None:
        """Remove an abandoned run's snapshots, unless it has both: a run that died after
        its files snapshot but before it was recorded is a whole back-up and is kept.
        When the listing fails nothing is removed."""
        try:
            listed = restic.parse_snapshots(
                self(restic.snapshots_argv(f"run:{run_id}"), timeout=SHORT_RESTIC_TIMEOUT)
            )
        except BackupError as error:
            logger.warning("Backup run %s: listing its snapshots failed: %s", run_id, error.code)
            return
        kinds = {tag for snapshot in listed for tag in snapshot.tags if tag.startswith("kind:")}
        if {"kind:db", "kind:files"} <= kinds:
            logger.warning("Backup run %s was abandoned but is whole: kept", run_id)
            return
        self.forget_run(run_id)

    def forget_run(self, run_id: UUID) -> None:
        """Remove the snapshots of one run; a failure is logged (code only), never raised."""
        if settings.BRIEVIE_BACKUP_APPEND_ONLY:
            return
        try:
            self(restic.forget_run_argv(run_id, self.host), timeout=SHORT_RESTIC_TIMEOUT)
        except BackupError as error:
            logger.warning("Backup run %s: removing its snapshots failed: %s", run_id, error.code)


def _database_conninfo() -> str:
    # The database the app uses, read as the back-up role.
    return backup_conninfo(dbname=str(connection.settings_dict["NAME"]))


class _IncompleteBackupError(BackupError):
    """The files snapshot was made, but restic could not read some files (exit 3)."""

    def __init__(self, size_bytes: int | None) -> None:
        super().__init__("files_incomplete")
        self.size_bytes = size_bytes


def _summary_or_none(stdout: bytes) -> int | None:
    try:
        return restic.summary_bytes(stdout)
    except BackupError:
        return None


def _back_up(
    run: BackupRun, failures: list[str], *, resumed: bool, clean_up: Sequence[UUID]
) -> None:
    """Steps 3-10 of a run, under the back-up lock (D 4.3: database first, then files).

    Only `unlock` and the clean-up have a timeout: killing restic during the dump
    would orphan the pg_dump it started, and the back-up, prune and check of a
    large archive can take hours.
    """
    with backup_lock():
        repository = configured_repository()
        host = backup_host()
        run_restic = _Restic(repository, backup_password(), failures, host)
        run_restic(restic.unlock_argv(), timeout=SHORT_RESTIC_TIMEOUT)
        for abandoned_id in clean_up:
            run_restic.forget_run_unless_whole(abandoned_id)
        if run.kind == BackupRun.Kind.CHECK:
            run_restic(restic.check_argv())
            _finish(run.id, BackupRun.Status.SUCCEEDED)
            return

        # A run that could not be restored is never recorded as succeeded.
        _require_env_copy()
        conninfo = _database_conninfo()
        require_backup_role(conninfo)
        require_client_for(conninfo)
        if resumed:
            # An earlier attempt of this run may have left snapshots: never two dumps.
            run_restic.forget_run(run.id)
        # One run, one time: both halves carry the run's start, so retention can never
        # keep one half of a run and drop the other.
        started = _started_at(run.id)
        safety = run.requested_by == RESTORE_SAFETY
        # The manifest counts in the transaction whose snapshot pg_dump dumps: a letter
        # written or deleted during the dump changes neither. The transaction stays
        # open on its own connection (never Django's) until the dump has ended.
        with counting_transaction(conninfo) as counting:
            snapshot_id = export_snapshot(counting)
            manifest = build_manifest(
                run_id=run.id,
                conn=counting,
                storage_root=Path(settings.BRIEVIE_STORAGE_ROOT),
                authority_dir=Path(settings.BRIEVIE_AUTHORITY_COPY_DIR),
            )
            dump_out = run_restic(
                restic.backup_stdin_argv(
                    run.id,
                    [*DUMP_COMMAND, f"--snapshot={snapshot_id}"],
                    host=host,
                    at=started,
                    safety=safety,
                ),
                extra=libpq_env(conninfo),
            )
        try:
            size = restic.summary_bytes(dump_out)
            write_private(
                Path(settings.BRIEVIE_BACKUP_CONFIG_DIR),
                MANIFEST_NAME,
                manifest_to_json(manifest),
            )
            files = run_restic.result(
                restic.backup_files_argv(
                    run.id,
                    file_paths(),
                    host=host,
                    at=started,
                    safety=safety,
                    excludes=file_excludes(),
                )
            )
            incomplete = restic.classify(files) == "files_incomplete"
            if not incomplete:
                restic.raise_for(files)
                files_size: int | None = restic.summary_bytes(files.stdout)
            else:
                files_size = _summary_or_none(files.stdout)
        except Exception:
            # Retention groups by host and paths: a database snapshot without its
            # files would be kept as if it were a whole back-up.
            run_restic.forget_run(run.id)
            raise
        total = None if files_size is None else size + files_size
        if incomplete:
            # Kept: a file that stays unreadable must not mean no back-up is ever kept.
            if _prunes(run):
                try:
                    _apply_retention(run_restic, run.id)
                except BackupError as error:
                    logger.warning("Backup run %s: retention failed: %s", run.id, error.code)
            raise _IncompleteBackupError(total)
        warning = _apply_retention(run_restic, run.id) if _prunes(run) else None
        fields: dict[str, object] = {"size_bytes": total}
        if warning is not None:
            fields.update(reason_code=warning, reason=str(REASONS[warning]))
        _finish(run.id, BackupRun.Status.SUCCEEDED, **fields)


# A snapshot dated this far ahead of now means a wrong clock somewhere: retention is
# relative to the newest snapshot, so it would remove almost everything.
CLOCK_TOLERANCE = timedelta(hours=24)
# A run with only one of its halves, older than this, is a leftover (removed first).
HALF_RUN_AGE = timedelta(hours=48)


def _started_at(run_id: UUID) -> datetime:
    started = BackupRun.objects.filter(id=run_id).values_list("started_at", flat=True).first()
    return started or timezone.now()


def _require_env_copy() -> None:
    """Refuse (`env_copy_missing`) a back-up without the .env copy: `fetch` needs it."""
    config = safepaths.configured_path(settings.BRIEVIE_BACKUP_CONFIG_DIR)
    safepaths.require_emptiable(config, allowed=[config], fixed=[safepaths.BACKUP_CONFIG])
    try:
        descriptor = safepaths.open_dir_nofollow(config)
    except FileNotFoundError:
        raise BackupError("env_copy_missing") from None
    try:
        info = os.stat(ENV_COPY_NAME, dir_fd=descriptor, follow_symlinks=False)
    except FileNotFoundError:
        raise BackupError("env_copy_missing") from None
    finally:
        os.close(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise BackupError("env_copy_missing")


def _half_runs(snapshots: list[restic.Snapshot], before: datetime) -> list[UUID]:
    """Runs that have only one of their two snapshots, all of them older than `before`."""
    kinds: dict[UUID, set[str]] = {}
    newest: dict[UUID, datetime] = {}
    for snapshot in snapshots:
        run_tags = [tag.removeprefix("run:") for tag in snapshot.tags if tag.startswith("run:")]
        try:
            run_id = UUID(run_tags[0]) if run_tags else None
        except ValueError:
            run_id = None
        if run_id is None:
            continue
        kinds.setdefault(run_id, set()).update(
            tag for tag in snapshot.tags if tag in ("kind:db", "kind:files")
        )
        newest[run_id] = max(newest.get(run_id, snapshot.time), snapshot.time)
    return sorted(
        (run_id for run_id, found in kinds.items() if len(found) < 2 and newest[run_id] < before),
        key=str,
    )


def _apply_retention(run_restic: "_Restic", current: UUID) -> str | None:
    """Retention of this installation's snapshots; returns a warning code, or None.

    First the clock: a snapshot more than a day ahead of now stops it (`clock_skew`),
    nothing is removed. Then half runs older than two days go by their run tag, then
    safety snapshots older than 30 days, then the ordinary retention (which keeps
    safety snapshots and everything of the last two days).
    """
    listed = restic.parse_snapshots(
        run_restic(restic.snapshots_host_argv(run_restic.host), timeout=SHORT_RESTIC_TIMEOUT)
    )
    now = timezone.now()
    if listed and max(snapshot.time for snapshot in listed) > now + CLOCK_TOLERANCE:
        logger.warning("Backup run %s: a snapshot lies in the future; no retention", current)
        return "clock_skew"
    for half in _half_runs(listed, now - HALF_RUN_AGE):
        if half != current:
            run_restic.forget_run(half)
    run_restic(restic.forget_safety_argv(run_restic.host))
    run_restic(restic.forget_argv(run_restic.host))
    return None


# The safety back-up that a restore makes right before it replaces anything.
RESTORE_SAFETY = "restore"


def _prunes(run: BackupRun) -> bool:
    """Whether this run applies retention: never in append-only mode, and never for the
    safety back-up of a restore (it must not remove the run the restore will use)."""
    return not settings.BRIEVIE_BACKUP_APPEND_ONLY and run.requested_by != RESTORE_SAFETY


def _record_failure(run_id: UUID, code: str, *, size_bytes: int | None = None) -> None:
    try:
        _finish(
            run_id,
            BackupRun.Status.FAILED,
            reason_code=code,
            reason=str(REASONS[code]),
            size_bytes=size_bytes,
        )
    except Exception as error:  # noqa: BLE001 - the job must end; request_run recovers the run
        logger.error("Backup run %s: recording the failure raised %s", run_id, type(error).__name__)


def perform_run(run_id: UUID, *, clean_up: Sequence[UUID] = ()) -> None:
    """Make or check a back-up; the job's whole body. Never raises: no automatic retry.

    Not one transaction: it runs external programs for hours. Each status change is
    its own short transaction. When even recording the end fails, the run stays
    open without a job, and the next `request_run` closes it.
    """
    run = BackupRun.objects.filter(id=run_id).first()
    if run is None or run.status not in OPEN_STATUSES:
        return
    resumed = run.status == BackupRun.Status.RUNNING
    failures: list[str] = []
    try:
        _mark_running(run_id)
        _back_up(run, failures, resumed=resumed, clean_up=clean_up)
    except BackupError as error:
        size = error.size_bytes if isinstance(error, _IncompleteBackupError) else None
        _record_failure(run_id, error.code, size_bytes=size)
        logger.warning("Backup run %s failed: %s", run_id, error.code)
        for stderr in failures:
            # Never stdout: `backup --json` may carry messages there.
            logger.warning("Backup run %s restic stderr: %s", run_id, stderr)
    except Exception as error:  # noqa: BLE001 - every failure must end in a visible status
        # The type only: a message can quote a path or a connection string.
        logger.error("Backup run %s failed with an unexpected %s", run_id, type(error).__name__)
        _record_failure(run_id, "internal_error")


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
    # Abandoned, or closed by a restore; only when newer than the last success.
    last_interrupted: BackupRun | None
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
    last_success = runs.filter(
        kind=BackupRun.Kind.BACKUP, status=BackupRun.Status.SUCCEEDED
    ).first()
    interrupted = runs.filter(status=BackupRun.Status.INTERRUPTED)
    if last_success is not None:
        interrupted = interrupted.filter(created_at__gt=last_success.created_at)
    return BackupStatus(
        brievie_version=brievie_version(),
        postgres_version=_postgres_version(),
        pg_dump_version=_tool_version(["pg_dump", "--version"], _PG_DUMP_VERSION),
        restic_version=_tool_version([settings.BRIEVIE_RESTIC_BINARY, "version"], _RESTIC_VERSION),
        destination_kind=_destination_kind_for_status(),
        last_success=last_success,
        last_check=runs.filter(
            kind=BackupRun.Kind.CHECK, status=BackupRun.Status.SUCCEEDED
        ).first(),
        last_failure=runs.filter(status=BackupRun.Status.FAILED).first(),
        last_interrupted=interrupted.first(),
        open_run=runs.filter(status__in=OPEN_STATUSES).first(),
    )

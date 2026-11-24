"""The nightly back-up and what the web interface shows and asks of the back-up.

A compose install has no terminal, so the keeper makes a back-up every night as soon as a
destination is set, and a server admin sees the state and asks for one from the web
interface. Nothing here returns a path, a key, a credential or a repository address.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import QuerySet
from django.utils import timezone

from apps.backup import services
from apps.backup.destination import UPDATE_FALLBACK_KIND, destination_kind
from apps.backup.errors import BackupError
from apps.backup.models import OPEN_STATUSES, BackupRun

# One back-up in 20 hours is enough for the nightly one, whoever made the last.
NIGHTLY_MIN_AGE = timedelta(hours=20)
# Past this a back-up is late: the panel says so (a night missed is not yet late).
LATE_AFTER = timedelta(hours=36)
SCHEDULE = "schedule"
WEB = "web"


def nightly_due(*, now: datetime, last_success_at: datetime | None) -> bool:
    """Pure: whether the nightly back-up should be requested (a destination is set)."""
    return last_success_at is None or now - last_success_at >= NIGHTLY_MIN_AGE


def _kind() -> str:
    """The destination kind of this server; `""` when none is set or the setting is wrong."""
    try:
        return destination_kind()
    except BackupError:
        return ""


def _real_backups() -> QuerySet[BackupRun]:
    # An update's emergency fallback is no back-up: it never counts here (as in status_summary).
    return BackupRun.objects.exclude(destination_kind=UPDATE_FALLBACK_KIND).filter(
        kind=BackupRun.Kind.BACKUP
    )


def request_nightly(*, now: datetime | None = None) -> BackupRun | None:
    """Ask for the nightly back-up: nothing without a destination, nothing when one finished
    in the last 20 hours; an open run is the answer when there is one."""
    now = now or timezone.now()
    if not _kind():
        return None
    last = _real_backups().filter(status=BackupRun.Status.SUCCEEDED).first()
    if not nightly_due(now=now, last_success_at=last.finished_at if last else None):
        return None
    return services.request_run(BackupRun.Kind.BACKUP, requested_by=SCHEDULE)


def request_manual() -> BackupRun:
    """ "Nu back-uppen": one run now. `not_configured` without a destination, `backup_running`
    while one is open (nothing is queued twice)."""
    if not _kind():
        raise BackupError("not_configured")
    if _real_backups().filter(status__in=OPEN_STATUSES).exists():
        raise BackupError("backup_running")
    return services.request_run(BackupRun.Kind.BACKUP, requested_by=WEB)


@dataclass(frozen=True)
class WebStatus:
    destination: str  # none | folder | remote | invalid
    last_success_at: datetime | None
    last_success_size_bytes: int | None
    last_failure_at: datetime | None
    last_failure_reason: str
    last_check_at: datetime | None
    running: bool
    late: bool


def _destination_word() -> str:
    try:
        kind = destination_kind()
    except BackupError:
        return "invalid"
    return {"": "none", "folder": "folder"}.get(kind, "remote")


def status(*, now: datetime | None = None) -> WebStatus:
    now = now or timezone.now()
    word = _destination_word()
    backups = _real_backups()
    success = backups.filter(status=BackupRun.Status.SUCCEEDED).first()
    failure = backups.filter(status=BackupRun.Status.FAILED).first()
    # A failure older than the last success is history, not news.
    if failure is not None and success is not None and failure.created_at < success.created_at:
        failure = None
    check = (
        BackupRun.objects.exclude(destination_kind=UPDATE_FALLBACK_KIND)
        .filter(kind=BackupRun.Kind.CHECK, status=BackupRun.Status.SUCCEEDED)
        .first()
    )
    last_at = success.finished_at if success else None
    late = word in ("folder", "remote") and (last_at is None or now - last_at > LATE_AFTER)
    return WebStatus(
        destination=word,
        last_success_at=last_at,
        last_success_size_bytes=success.size_bytes if success else None,
        last_failure_at=failure.finished_at if failure else None,
        last_failure_reason=failure.reason if failure else "",
        last_check_at=check.finished_at if check else None,
        running=backups.filter(status__in=OPEN_STATUSES).exists(),
        late=late,
    )

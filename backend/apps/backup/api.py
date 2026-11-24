"""Back-up things for server admins, from the web interface (a compose install has no
terminal): the recovery sheet. Web session only: a device token answers 401."""

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from ninja import Router, Status

from apps.accounts.auth import session_auth
from apps.accounts.second_factor_policy import has_factor
from apps.accounts.services import current_user
from apps.accounts.sessions import recently_authenticated, session_passed_second_factor
from apps.audit.codes import AuditCode
from apps.audit.services import actor_from_request, record
from apps.backup import schedule, sheet
from apps.backup.errors import REASONS, BackupError
from apps.backup.schemas import BackupRunOut, BackupStatusOut
from apps.common.errors import ApiError
from apps.common.schemas import AUTH_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import CallerThrottle

router = Router(tags=["backup"])

SHEET_FILE_NAME = "brievie-herstelblad.html"
_HTML_RESPONSE = {
    "responses": {
        200: {
            "description": "The recovery sheet, a printable HTML page",
            "content": {"text/html": {"schema": {"type": "string"}}},
        }
    }
}


@router.post(
    "/server/recovery-sheet",
    response={403: ErrorOut, 409: ErrorOut, 429: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    # The sheet carries the master key: a handful an hour is plenty for a person.
    throttle=CallerThrottle("recovery_sheet", "6/h"),
    operation_id="createRecoverySheet",
    openapi_extra=_HTML_RESPONSE,
    description=(
        "Web session only: a bearer token answers 401 not_authenticated; a session without "
        "a valid CSRF token answers 403. Server admins only (403 forbidden). The sheet "
        "carries the master key: the session must have signed in or called reauthenticate "
        "within 15 minutes (403 reauthentication_required), and with two-step verification "
        "on it must have passed the code (403 second_factor_session_required). Answers the "
        "page as a download "
        "(Cache-Control: no-store) and records recovery_sheet_viewed in the log. 409 "
        "recovery_sheet_not_here on a server that is not a compose install: there the sheet "
        "is made on the server with ./brievie recovery-sheet. POST, never GET: a GET would "
        "be cached and logged on the way."
    ),
)
def create_recovery_sheet(request: HttpRequest) -> HttpResponse:
    _server_admin(request)
    if not settings.BRIEVIE_COMPOSE_INSTALL:
        raise ApiError(
            409,
            "recovery_sheet_not_here",
            _("Op deze server maak je het herstelblad met ./brievie recovery-sheet."),
        )
    if not recently_authenticated(request):
        raise ApiError(403, "reauthentication_required", _("Vul eerst je wachtwoord opnieuw in."))
    # The master key, so as pairing: with two-step verification on, only a session that
    # passed the code (security review of PR #52, point 6).
    if has_factor(current_user(request)) and not session_passed_second_factor(request):
        raise ApiError(
            403,
            "second_factor_session_required",
            _("Log opnieuw in met je code om dit te doen."),
        )
    # Rendering first: a sheet that cannot be made is not recorded as shown. The key is
    # in `page` only; nothing below logs or echoes it.
    page = sheet.render_sheet(sheet.sheet_data())
    record(AuditCode.RECOVERY_SHEET_VIEWED, actor=actor_from_request(request))
    response = HttpResponse(page, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["Content-Disposition"] = f'attachment; filename="{SHEET_FILE_NAME}"'
    return response


def _server_admin(request: HttpRequest) -> None:
    user = current_user(request)
    if not (user.is_active and user.is_server_admin):
        raise ApiError(403, "forbidden", _("Alleen een serverbeheerder mag dit doen."))


@router.get(
    "/server/backup-status",
    response={200: BackupStatusOut, 403: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    operation_id="getBackupStatus",
    description=(
        "Web session only: a bearer token answers 401 not_authenticated. Server admins "
        "only. What the back-up panel shows: the kind of destination (never its path or "
        "address), the last good back-up and the last failure since, and whether one is "
        "running or late."
    ),
)
def get_backup_status(request: HttpRequest) -> BackupStatusOut:
    _server_admin(request)
    found = schedule.status()
    return BackupStatusOut(
        destination=found.destination,
        last_success_at=found.last_success_at,
        last_success_size_bytes=found.last_success_size_bytes,
        last_failure_at=found.last_failure_at,
        last_failure_reason=found.last_failure_reason,
        last_check_at=found.last_check_at,
        running=found.running,
        late=found.late,
    )


@router.post(
    "/server/backup-runs",
    response={
        202: BackupRunOut,
        403: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
    },
    auth=session_auth,
    throttle=CallerThrottle("backup_run", "10/h"),
    operation_id="createBackupRun",
    description=(
        "Web session only: a bearer token answers 401 not_authenticated; a session without "
        "a valid CSRF token answers 403. Server admins only. Queues one back-up now (the "
        "keeper makes it) and records backup_requested in the log. 409 not_configured without "
        "a destination; 409 backup_running while one is open."
    ),
)
def create_backup_run(request: HttpRequest) -> Status[BackupRunOut]:
    _server_admin(request)
    try:
        run = schedule.request_manual()
    except BackupError as error:
        raise ApiError(409, error.code, str(REASONS[error.code])) from None
    record(AuditCode.BACKUP_REQUESTED, actor=actor_from_request(request))
    return Status(202, BackupRunOut(run_id=run.id))

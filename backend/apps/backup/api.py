"""Back-up things for server admins, from the web interface (a compose install has no
terminal): the recovery sheet. Web session only: a device token answers 401."""

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from ninja import Router

from apps.accounts.auth import session_auth
from apps.accounts.services import current_user
from apps.accounts.sessions import recently_authenticated
from apps.audit.codes import AuditCode
from apps.audit.services import actor_from_request, record
from apps.backup import sheet
from apps.common.errors import ApiError
from apps.common.schemas import SESSION_WRITE_ERRORS, ErrorOut

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
    response={403: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    operation_id="createRecoverySheet",
    openapi_extra=_HTML_RESPONSE,
    description=(
        "Web session only: a bearer token answers 401 not_authenticated; a session without "
        "a valid CSRF token answers 403. Server admins only (403 forbidden). The sheet "
        "carries the master key: the session must have signed in or called reauthenticate "
        "within 15 minutes (403 reauthentication_required). Answers the page as a download "
        "(Cache-Control: no-store) and records recovery_sheet_viewed in the log. 409 "
        "recovery_sheet_not_here on a server that is not a compose install: there the sheet "
        "is made on the server with ./brievie recovery-sheet. POST, never GET: a GET would "
        "be cached and logged on the way."
    ),
)
def create_recovery_sheet(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    if not (user.is_active and user.is_server_admin):
        raise ApiError(403, "forbidden", _("Alleen een serverbeheerder mag dit doen."))
    if not settings.BRIEVIE_COMPOSE_INSTALL:
        raise ApiError(
            409,
            "recovery_sheet_not_here",
            _("Op deze server maak je het herstelblad met ./brievie recovery-sheet."),
        )
    if not recently_authenticated(request):
        raise ApiError(403, "reauthentication_required", _("Vul eerst je wachtwoord opnieuw in."))
    # Rendering first: a sheet that cannot be made is not recorded as shown. The key is
    # in `page` only; nothing below logs or echoes it.
    page = sheet.render_sheet(sheet.sheet_data())
    record(AuditCode.RECOVERY_SHEET_VIEWED, actor=actor_from_request(request))
    response = HttpResponse(page, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["Content-Disposition"] = f'attachment; filename="{SHEET_FILE_NAME}"'
    return response

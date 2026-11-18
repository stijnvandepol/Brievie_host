"""Reading the audit logs (D 11): the workspace's by its admins, the server's by server
admins. Web session only; a device token answers 401. Nothing here writes a row."""

import csv
import io
from collections.abc import Sequence
from datetime import UTC, date, datetime
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from ninja import Router

from apps.accounts.auth import session_auth
from apps.accounts.services import current_user
from apps.audit import services
from apps.audit.codes import AuditCode
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.audit.schemas import AuditEventOut
from apps.audit.services import AuditFilter
from apps.common.errors import ApiError
from apps.common.schemas import AUTH_ERRORS, ErrorOut
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["audit"])

_WORKSPACE = "/workspaces/{workspace_id}/audit-events"
_SERVER = "/server/audit-events"

_FILTERS = (
    " Filters: `user_id` (rows where the user acted or was acted on), `code` (one of the"
    " log's codes, else 422), `since` and `until` (whole days in Amsterdam time, both"
    " inclusive), `before` (rows strictly older than this moment: pass the last row's"
    " `created_at` for the next page). Newest first."
)
_WEB_ONLY = "Web session only: a bearer token answers 401 not_authenticated."
_CSV_RESPONSE = {
    "responses": {
        200: {
            "description": "The log as CSV",
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    }
}
_CSV_NOTE = (
    f" Up to {services.CSV_MAX_ROWS:,} rows with the same filters (`limit` does not apply);"
    " when more match, the newest are given, the header `X-Brievie-Truncated: true` is set"
    " and the last line says so. A cell that would start a spreadsheet formula gets a"
    " leading apostrophe."
)


def _invalid(message: str) -> ApiError:
    return ApiError(422, "validation_error", message)


def _filter(
    *,
    user_id: UUID | None,
    code: str,
    since: date | None,
    until: date | None,
    before: datetime | None,
    limit: int,
    limit_cap: int = services.MAX_LIMIT,
) -> AuditFilter:
    """The checked filter: a code of the closed list, an ordered date range, a bounded page."""
    if code and code not in AuditCode.values:
        raise _invalid(_("Deze gebeurtenis kennen we niet."))
    if since is not None and until is not None and since > until:
        raise _invalid(_("De begindatum ligt na de einddatum."))
    if not 1 <= limit <= max(limit_cap, 1):
        raise _invalid(
            _("Vraag tussen 1 en %(max)d gebeurtenissen tegelijk op.") % {"max": limit_cap}
        )
    if before is not None and timezone.is_naive(before):
        before = before.replace(tzinfo=UTC)
    return AuditFilter(
        user_id=user_id, code=code, since=since, until=until, before=before, limit=limit
    )


def _server_admin(request: HttpRequest) -> None:
    user = current_user(request)
    if not (user.is_active and user.is_server_admin):
        raise ApiError(403, "forbidden", _("Alleen een serverbeheerder mag dit doen."))


def _csv_response(
    events: Sequence[WorkspaceAuditEvent] | Sequence[ServerAuditEvent],
) -> HttpResponse:
    """The CSV of the newest `CSV_MAX_ROWS` of `events` (one more than that was read, to
    know whether the file is cut short: then it says so, in a header and a last line)."""
    cap = services.CSV_MAX_ROWS
    truncated = len(events) > cap
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerows(services.csv_rows(events[:cap]))
    if truncated:
        writer.writerow(
            [
                _("Let op: alleen de nieuwste %(count)d gebeurtenissen staan in dit bestand.")
                % {"count": cap}
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    name = f"brievie-logboek-{timezone.localdate(timezone=services.LOG_TIME_ZONE).isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "private, no-store"
    if truncated:
        response["X-Brievie-Truncated"] = "true"
    return response


@router.get(
    _WORKSPACE,
    response={200: list[AuditEventOut], 404: ErrorOut, 422: ErrorOut, 403: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    operation_id="listWorkspaceAuditEvents",
    description=f"{_WEB_ONLY} The workspace's log, for its admins only (another role answers"
    f" 403 forbidden). At most `limit` rows (1 to {services.MAX_LIMIT}, default"
    f" {services.DEFAULT_LIMIT}).{_FILTERS}",
)
def list_workspace_audit_events(
    request: HttpRequest,
    workspace_id: UUID,
    user_id: UUID | None = None,
    code: str = "",
    since: date | None = None,
    until: date | None = None,
    before: datetime | None = None,
    limit: int = services.DEFAULT_LIMIT,
) -> list[AuditEventOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    checked = _filter(
        user_id=user_id, code=code, since=since, until=until, before=before, limit=limit
    )
    events = services.list_workspace_events(membership.workspace, checked)
    return [AuditEventOut.from_event(e) for e in events]


@router.get(
    f"{_WORKSPACE}/csv",
    response={404: ErrorOut, 422: ErrorOut, 403: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    operation_id="exportWorkspaceAuditEvents",
    openapi_extra=_CSV_RESPONSE,
    description=f"{_WEB_ONLY} The workspace's log as a CSV download, for its admins only.{_FILTERS}"
    f"{_CSV_NOTE}",
)
def export_workspace_audit_events(
    request: HttpRequest,
    workspace_id: UUID,
    user_id: UUID | None = None,
    code: str = "",
    since: date | None = None,
    until: date | None = None,
    before: datetime | None = None,
) -> HttpResponse:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    checked = _filter(
        user_id=user_id,
        code=code,
        since=since,
        until=until,
        before=before,
        limit=services.CSV_MAX_ROWS + 1,
        limit_cap=services.CSV_MAX_ROWS + 1,
    )
    return _csv_response(services.list_workspace_events(membership.workspace, checked))


@router.get(
    _SERVER,
    response={200: list[AuditEventOut], 403: ErrorOut, 422: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    operation_id="listServerAuditEvents",
    description=f"{_WEB_ONLY} The server's log (accounts, sessions, devices), for server"
    f" admins only (else 403 forbidden).{_FILTERS}",
)
def list_server_audit_events(
    request: HttpRequest,
    user_id: UUID | None = None,
    code: str = "",
    since: date | None = None,
    until: date | None = None,
    before: datetime | None = None,
    limit: int = services.DEFAULT_LIMIT,
) -> list[AuditEventOut]:
    _server_admin(request)
    checked = _filter(
        user_id=user_id, code=code, since=since, until=until, before=before, limit=limit
    )
    return [AuditEventOut.from_event(e) for e in services.list_server_events(checked)]


@router.get(
    f"{_SERVER}/csv",
    response={403: ErrorOut, 422: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    operation_id="exportServerAuditEvents",
    openapi_extra=_CSV_RESPONSE,
    description=f"{_WEB_ONLY} The server's log as a CSV download, for server admins only.{_FILTERS}"
    f"{_CSV_NOTE}",
)
def export_server_audit_events(
    request: HttpRequest,
    user_id: UUID | None = None,
    code: str = "",
    since: date | None = None,
    until: date | None = None,
    before: datetime | None = None,
) -> HttpResponse:
    _server_admin(request)
    checked = _filter(
        user_id=user_id,
        code=code,
        since=since,
        until=until,
        before=before,
        limit=services.CSV_MAX_ROWS + 1,
        limit_cap=services.CSV_MAX_ROWS + 1,
    )
    return _csv_response(services.list_server_events(checked))

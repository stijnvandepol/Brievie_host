"""Writing the audit log. Every service that does something in D 11's list calls `record`.

`record` accepts only a code of the closed list and, per code, only the value keys
of `ALLOWED_VALUES`; values are short plain text, numbers or flags. So no row can hold
document content, a label name, a token or a password unless a code is made for it.
"""

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import models
from django.http import HttpRequest
from django.utils import timezone

from apps.audit.codes import ALLOWED_VALUES, NAMED_TARGETS, ActorKind, AuditCode, TargetKind
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.common.net import usable_ip, valid_client_ip
from apps.common.privacy import mask_bsns
from apps.workspaces.context import NOBODY, workspace_context

if TYPE_CHECKING:
    from apps.accounts.models import User
    from apps.workspaces.models import Workspace

MAX_VALUE_LENGTH = 200
MAX_NAME_LENGTH = 150

AuditValue = str | int | bool


@dataclass(frozen=True)
class AuditActor:
    """Who did it: a user (browser), a user's device (bearer token), the system or SSO.

    `name` is always a person's name (the user's, also for their device), "Brievie" for
    the system, or empty: every actor kind may carry one.
    """

    kind: str
    user_id: UUID | None
    name: str
    device_id: UUID | None = None
    client_ip: str | None = None

    @classmethod
    def system(cls) -> "AuditActor":
        return cls(kind=ActorKind.SYSTEM, user_id=None, name="Brievie")

    @classmethod
    def for_user(cls, user: "User", *, client_ip: str | None = None) -> "AuditActor":
        return cls(kind=ActorKind.USER, user_id=user.id, name=user.name, client_ip=client_ip)


@dataclass(frozen=True)
class AuditTarget:
    """What it was done to: a kind of `TargetKind`, an id and, for `NAMED_TARGETS` only,
    a name. A label, rule, document, export or session is an id only."""

    kind: str
    id: UUID | None
    name: str = ""


def actor_from_request(request: HttpRequest) -> AuditActor:
    """The signed-in caller; a paired device when the request came with its token."""
    address = valid_client_ip(request)
    user = request.user
    user_id: UUID | None = user.pk if user.is_authenticated else None
    name: str = getattr(user, "name", "") if user.is_authenticated else ""
    device = getattr(request, "device", None)
    if device is not None:
        return AuditActor(
            kind=ActorKind.DEVICE,
            user_id=user_id,
            name=name,
            device_id=device.id,
            client_ip=address,
        )
    return AuditActor(kind=ActorKind.USER, user_id=user_id, name=name, client_ip=address)


def record(
    code: AuditCode,
    *,
    actor: AuditActor,
    target: AuditTarget | None = None,
    workspace_id: UUID | None = None,
    values: Mapping[str, AuditValue] | None = None,
) -> None:
    """Write one audit row: a workspace row when `workspace_id` is given, else a server row.

    Raises `ValueError` for a code outside the closed list, an unknown actor or target
    kind, a name on a target that carries an id only (see `NAMED_TARGETS`), a value key
    the code does not allow, or a value that is not text, a number or a flag. Text is
    cut to 200 characters (names to 150) and BSN-masked; an address `inet` cannot hold
    is stored as NULL.
    """
    code = AuditCode(code)
    if actor.kind not in ActorKind.values:
        raise ValueError(f"Unknown audit actor kind: {actor.kind!r}")
    if target is not None and target.kind not in TargetKind.values:
        raise ValueError(f"Unknown audit target kind: {target.kind!r}")
    if target is not None and target.name and target.kind not in NAMED_TARGETS:
        raise ValueError(f"An audit target of kind {target.kind!r} carries an id only, no name")
    fields = {
        "actor_kind": actor.kind,
        "actor_id": actor.user_id,
        "actor_name": _name(actor.name),
        "actor_device_id": actor.device_id,
        "code": code.value,
        "target_kind": target.kind if target else "",
        "target_id": target.id if target else None,
        "target_name": _name(target.name) if target else "",
        # A caller may pass a forged forwarded address; inet would refuse it and abort
        # the caller's transaction.
        "client_ip": usable_ip(actor.client_ip),
        "values": _clean_values(code, values or {}),
    }
    if workspace_id is None:
        ServerAuditEvent.objects.create(**fields)
        return
    with workspace_context(workspace_id, actor=NOBODY):
        WorkspaceAuditEvent.objects.create(workspace_id=workspace_id, **fields)


def _name(name: str) -> str:
    return mask_bsns(name)[:MAX_NAME_LENGTH]


def _clean_values(code: AuditCode, values: Mapping[str, AuditValue]) -> dict[str, AuditValue]:
    allowed = ALLOWED_VALUES[code]
    cleaned: dict[str, AuditValue] = {}
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(f"Audit code {code.value} does not take the value {key!r}")
        if isinstance(value, str):
            cleaned[key] = mask_bsns(value)[:MAX_VALUE_LENGTH]
        elif isinstance(value, bool | int):
            cleaned[key] = value
        else:
            raise ValueError(f"Audit value {key!r} must be text, a number or a flag")
    return cleaned


# The log is read by whole days as the people in it live them.
LOG_TIME_ZONE = ZoneInfo("Europe/Amsterdam")
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
# The most rows one CSV download holds.
CSV_MAX_ROWS = 10_000
CSV_HEADER = (
    "Tijdstip",
    "Wie",
    "Gebeurtenis",
    "Code",
    "Om wie of wat",
    "Adres",
    "Details",
)
# A cell that starts with one of these is a formula to a spreadsheet.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


@dataclass(frozen=True)
class AuditFilter:
    """What to read from a log. `user_id` matches the rows where the user acted or was
    acted on; `since` and `until` are whole days in Amsterdam, both inclusive; `before`
    pages backwards (rows strictly older than it); `code` is empty for every code."""

    user_id: UUID | None = None
    code: str = ""
    since: date | None = None
    until: date | None = None
    before: datetime | None = None
    limit: int = DEFAULT_LIMIT


def _filtered[E: (WorkspaceAuditEvent, ServerAuditEvent)](
    rows: "models.QuerySet[E]", f: AuditFilter
) -> "models.QuerySet[E]":
    if f.user_id is not None:
        rows = rows.filter(models.Q(actor_id=f.user_id) | models.Q(target_id=f.user_id))
    if f.code:
        rows = rows.filter(code=f.code)
    if f.since is not None:
        rows = rows.filter(created_at__gte=datetime.combine(f.since, time.min, LOG_TIME_ZONE))
    if f.until is not None:
        end = datetime.combine(f.until + timedelta(days=1), time.min, LOG_TIME_ZONE)
        rows = rows.filter(created_at__lt=end)
    if f.before is not None:
        rows = rows.filter(created_at__lt=f.before)
    return rows.order_by("-created_at", "-id")[: f.limit]


def list_workspace_events(workspace: "Workspace", f: AuditFilter) -> list[WorkspaceAuditEvent]:
    """The workspace's newest rows first. Needs the workspace's RLS context, which then
    holds the rows of no other workspace whatever the filter says."""
    return list(_filtered(WorkspaceAuditEvent.objects.filter(workspace_id=workspace.id), f))


def list_server_events(f: AuditFilter) -> list[ServerAuditEvent]:
    return list(_filtered(ServerAuditEvent.objects.all(), f))


def code_label(code: str) -> str:
    """The Dutch label of a code; the code itself for one this version does not know."""
    return str(AuditCode(code).label) if code in AuditCode.values else code


def value_text(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


def _guard(cell: str) -> str:
    """Stop a spreadsheet from running a cell as a formula: a leading apostrophe."""
    return f"'{cell}" if cell.startswith(_FORMULA_STARTS) else cell


def csv_rows(events: Iterable[WorkspaceAuditEvent | ServerAuditEvent]) -> Iterator[list[str]]:
    """The header, then one row per event, every cell safe to open in a spreadsheet."""
    yield list(CSV_HEADER)
    for e in events:
        when = timezone.localtime(e.created_at, LOG_TIME_ZONE).strftime("%Y-%m-%d %H:%M:%S")
        target = e.target_name or (f"{e.target_kind} {e.target_id}" if e.target_id else "")
        details = "; ".join(f"{k}={value_text(v)}" for k, v in sorted(e.values.items()))
        yield [
            _guard(cell)
            for cell in (
                when,
                e.actor_name,
                code_label(e.code),
                e.code,
                target,
                e.client_ip or "",
                details,
            )
        ]

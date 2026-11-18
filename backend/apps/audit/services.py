"""Writing the audit log. Every service that does something in D 11's list calls `record`.

`record` accepts only a code of the closed list and, per code, only the value keys
of `ALLOWED_VALUES`; values are short plain text, numbers or flags. So no row can hold
document content, a label name, a token or a password unless a code is made for it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from django.http import HttpRequest

from apps.audit.codes import ALLOWED_VALUES, ActorKind, AuditCode, TargetKind
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.common.net import valid_client_ip
from apps.common.privacy import mask_bsns
from apps.workspaces.context import workspace_context

if TYPE_CHECKING:
    from apps.accounts.models import User

MAX_VALUE_LENGTH = 200
MAX_NAME_LENGTH = 150

AuditValue = str | int | bool


@dataclass(frozen=True)
class AuditActor:
    """Who did it: a user (browser), a user's device (bearer token), the system or SSO."""

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
    """What it was done to: a kind of `TargetKind`, an id and, for people, a name."""

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
    kind, a value key the code does not allow, or a value that is not text, a number
    or a flag. Text is cut to 200 characters (names to 150) and BSN-masked.
    """
    code = AuditCode(code)
    if actor.kind not in ActorKind.values:
        raise ValueError(f"Unknown audit actor kind: {actor.kind!r}")
    if target is not None and target.kind not in TargetKind.values:
        raise ValueError(f"Unknown audit target kind: {target.kind!r}")
    fields = {
        "actor_kind": actor.kind,
        "actor_id": actor.user_id,
        "actor_name": _name(actor.name),
        "actor_device_id": actor.device_id,
        "code": code.value,
        "target_kind": target.kind if target else "",
        "target_id": target.id if target else None,
        "target_name": _name(target.name) if target else "",
        "client_ip": actor.client_ip,
        "values": _clean_values(code, values or {}),
    }
    if workspace_id is None:
        ServerAuditEvent.objects.create(**fields)
        return
    with workspace_context(workspace_id):
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

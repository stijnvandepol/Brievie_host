from datetime import datetime
from uuid import UUID

from ninja import Schema

from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.audit.services import code_label, value_text


class AuditEventOut(Schema):
    id: UUID
    created_at: datetime
    # user, device, system or sso.
    actor_kind: str
    actor_id: UUID | None
    actor_name: str
    actor_device_id: UUID | None
    code: str
    # The code's Dutch label.
    label: str
    target_kind: str
    target_id: UUID | None
    target_name: str
    client_ip: str = ""
    # Small enumerations, counts and flags, all as text (`true`, `false`, `3`).
    values: dict[str, str]

    @classmethod
    def from_event(cls, event: WorkspaceAuditEvent | ServerAuditEvent) -> "AuditEventOut":
        return cls(
            id=event.id,
            created_at=event.created_at,
            actor_kind=event.actor_kind,
            actor_id=event.actor_id,
            actor_name=event.actor_name,
            actor_device_id=event.actor_device_id,
            code=event.code,
            label=code_label(event.code),
            target_kind=event.target_kind,
            target_id=event.target_id,
            target_name=event.target_name,
            client_ip=event.client_ip or "",
            values={key: value_text(value) for key, value in event.values.items()},
        )

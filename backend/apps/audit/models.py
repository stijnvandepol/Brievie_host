"""The audit log: two append-only tables (spec 1d, D 11).

The application's database role may insert and read these rows, never change or
delete them (revoked in migration 0001). The rows have no foreign keys: actor and
target are an id and a name as they were then, since a cascade would need the rights
that were revoked. Write them only through `apps.audit.services.record`.
"""

from typing import ClassVar

from django.db import models

from apps.audit.codes import ActorKind, AuditCode, TargetKind
from apps.common.models import BaseModel, WorkspaceModel


class AuditFields(models.Model):
    actor_kind = models.CharField(max_length=10, choices=ActorKind.choices)
    actor_id = models.UUIDField(null=True, blank=True)
    actor_name = models.CharField(max_length=150, blank=True)
    actor_device_id = models.UUIDField(null=True, blank=True)
    code = models.CharField(max_length=50, choices=AuditCode.choices)
    target_kind = models.CharField(max_length=20, choices=TargetKind.choices, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    target_name = models.CharField(max_length=150, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    values = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return f"{self.code} by {self.actor_kind} {self.actor_id}"


class WorkspaceAuditEvent(AuditFields, WorkspaceModel):
    """What happened in one workspace; read by its admins, under forced RLS."""

    # No foreign key (D 11): a deleted workspace must not need DELETE on this table.
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="+",
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["workspace", "-created_at"], name="audit_workspace_latest"),
        ]


class ServerAuditEvent(AuditFields, BaseModel):
    """What happened on the server outside a workspace; read by server admins."""

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["-created_at"], name="audit_server_latest"),
        ]

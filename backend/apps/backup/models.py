from typing import ClassVar

from django.db import models

from apps.common.models import BaseModel


class BackupRun(BaseModel):
    """One back-up, check (or later restore test) run. Not a workspace table."""

    class Kind(models.TextChoices):
        BACKUP = "backup"
        CHECK = "check"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        SUCCEEDED = "succeeded"
        FAILED = "failed"
        INTERRUPTED = "interrupted"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    requested_by = models.CharField(max_length=10, default="cli")
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    # The kind only, never a path: "" | folder | rest | s3.
    destination_kind = models.CharField(max_length=10, blank=True)
    reason_code = models.CharField(max_length=50, blank=True)
    reason = models.CharField(max_length=300, blank=True)
    size_bytes = models.BigIntegerField(null=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        indexes: ClassVar[list[models.Index]] = [models.Index(fields=["kind", "status"])]


OPEN_STATUSES = (BackupRun.Status.QUEUED, BackupRun.Status.RUNNING)

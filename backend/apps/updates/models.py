from typing import ClassVar

from django.db import models

from apps.common.models import BaseModel


class ServerUpdate(BaseModel):
    """One finished `./brievie update`: the history (in every back-up). Server-level, not a
    workspace table. What runs now is pinned by BRIEVIE_VERSION in deploy/.env."""

    from_version = models.CharField(max_length=20)
    to_version = models.CharField(max_length=20)
    # The update's emergency back-up (its run id), when there was one.
    backup_run_id = models.UUIDField(null=True)
    finished_at = models.DateTimeField()

    class Meta:
        ordering: ClassVar[list[str]] = ["-finished_at"]

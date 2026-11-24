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


class InstalledVersion(BaseModel):
    """The version this database belongs to: one row, written by `prepare_server` at every
    start and by nothing else (`services.set_installed_version`). Server-level, not a
    workspace table. It travels with every back-up, so a restore brings it back with the
    data it describes."""

    version = models.CharField(max_length=20)
    # Always true; the unique constraint on it keeps the table to one row.
    singleton = models.BooleanField(default=True, unique=True, editable=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(singleton=True), name="installedversion_singleton"
            )
        ]

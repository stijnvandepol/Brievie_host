import uuid

from django.db import models


class BaseModel(models.Model):
    """Every table has a UUID primary key and creation/update timestamps."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class WorkspaceModel(BaseModel):
    """Base for workspace content tables.

    Every concrete subclass must end its first migration with
    `apps.workspaces.rls.enable_rls(<table>)`; tests/test_rls_coverage.py checks this.
    """

    workspace = models.ForeignKey("workspaces.Workspace", on_delete=models.CASCADE, db_index=True)

    class Meta:
        abstract = True

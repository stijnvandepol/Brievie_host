import uuid
from collections.abc import Iterable
from typing import ClassVar

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models.base import ModelBase


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

    # Foreign keys to other workspace rows. The database's foreign-key check bypasses
    # RLS, so a row could point into another workspace; save() refuses that.
    same_workspace_fields: ClassVar[tuple[str, ...]] = ()

    class Meta:
        abstract = True

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        for field in self.same_workspace_fields:
            if getattr(self, f"{field}_id") is None:
                continue
            try:
                related_workspace_id = getattr(self, field).workspace_id
            except ObjectDoesNotExist:
                related_workspace_id = self._hidden_workspace_id(field)
            if related_workspace_id != self.workspace_id:
                raise ValueError(f"{field} must be in the same workspace.")
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def _hidden_workspace_id(self, field: str) -> object:
        """The workspace of a related row the actor may not see (a letter hidden by the
        second policy layer), read as the system. The workspace policy still binds, so a
        row of another workspace stays invisible: None, and save() refuses it."""
        from apps.workspaces.context import system_actor

        related = self._meta.get_field(field).related_model
        if not (isinstance(related, type) and issubclass(related, models.Model)):
            return None
        try:
            with system_actor():
                return (
                    related._base_manager.filter(pk=getattr(self, f"{field}_id"))
                    .values_list("workspace_id", flat=True)
                    .first()
                )
        except RuntimeError:
            # No workspace context at all: nothing is visible, nothing to compare.
            return None

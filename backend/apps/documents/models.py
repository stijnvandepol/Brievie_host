from collections.abc import Iterable
from typing import ClassVar

from django.db import models
from django.db.models.base import ModelBase

from apps.common.models import WorkspaceModel


class Folder(WorkspaceModel):
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    name = models.CharField(max_length=200)
    # Full path such as "/Auto/APK", derived from the parent on save.
    path = models.TextField(editable=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["workspace", "path"], name="documents_folder_workspace_path_unique"
            ),
        ]

    def __str__(self) -> str:
        return self.path

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if self.parent is not None and self.parent.workspace_id != self.workspace_id:
            # The FK check bypasses RLS, so a parent from another workspace would be accepted.
            raise ValueError("A folder's parent must be in the same workspace.")
        parent_path = self.parent.path if self.parent is not None else ""
        self.path = f"{parent_path}/{self.name}"
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

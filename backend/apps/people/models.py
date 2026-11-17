from collections.abc import Iterable
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models.base import ModelBase
from django.db.models.functions import Lower

from apps.common.models import WorkspaceModel
from apps.workspaces.models import Membership


class Person(WorkspaceModel):
    """Someone letters are addressed to; optionally a member of the workspace."""

    # Trimmed and BSN-masked (see people.services).
    display_name = models.CharField(max_length=150)
    # How the name is written on letters ("J. de Vries"): 0-20 strings of 2-100 characters.
    name_variants = models.JSONField(default=list, blank=True)
    # The linked member. Users are not workspace rows, so save() checks the membership.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # An archived person stays on their documents but is not offered, matched or proposed.
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                models.F("workspace"), Lower("display_name"), name="people_person_name_unique"
            ),
            models.UniqueConstraint(
                fields=["workspace", "user"],
                condition=models.Q(user__isnull=False),
                name="people_person_user_unique",
            ),
        ]

    def __str__(self) -> str:
        return str(self.id)

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if (
            self.user_id is not None
            and not Membership.objects.filter(
                workspace_id=self.workspace_id, user_id=self.user_id
            ).exists()
        ):
            raise ValueError("A person's user must be a member of the workspace.")
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

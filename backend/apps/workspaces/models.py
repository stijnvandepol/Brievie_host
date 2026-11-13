from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel


class Workspace(BaseModel):
    """Not under RLS: it is looked up before a workspace context exists."""

    class Kind(models.TextChoices):
        HOME = "home", _("Thuis")
        BUSINESS = "business", _("Zakelijk")

    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    modules = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:
        return self.name


class Membership(BaseModel):
    """Not under RLS: it decides which workspace context a request may enter."""

    class Role(models.TextChoices):
        ADMIN = "admin", _("Beheerder")
        MEMBER = "member", _("Lid")
        VIEWER = "viewer", _("Meekijker")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "workspace"], name="workspaces_membership_user_workspace_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.workspace_id} as {self.role}"

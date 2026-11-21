from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel, WorkspaceModel


class Workspace(BaseModel):
    """Not under RLS: it is looked up before a workspace context exists."""

    class Kind(models.TextChoices):
        HOME = "home", _("Thuis")
        BUSINESS = "business", _("Organisatie")

    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    modules = models.JSONField(default=dict, blank=True)
    # Whether a member may export their own post (D 6.3): on at home, off in Zakelijk.
    allow_member_export = models.BooleanField(default=True)
    # Who sees which letter (S 4.1). No `db_default` on any of them: Django would then
    # insert with RETURNING, which reads the new row through the SELECT policies.
    # "Iedereen ziet alle post": every member sees every letter that is not private.
    everyone_sees_all = models.BooleanField(default=True)
    # Departments receive post; off, their letters go to whoever sorts post.
    departments_enabled = models.BooleanField(default=False)
    # Admins see all post that is not private, also with `everyone_sees_all` off.
    admins_read_all = models.BooleanField(default=True)
    # A letter labelled Zorg becomes private on its own (S 4.6).
    care_is_private = models.BooleanField(default=False)
    # A member may ask for emergency access to someone's hidden post (S 4.7).
    emergency_access = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.name


class Membership(BaseModel):
    """Not under RLS: it decides which workspace context a request may enter."""

    class Role(models.TextChoices):
        ADMIN = "admin", _("Beheerder")
        MEMBER = "member", _("Lid")
        VIEWER = "viewer", _("Alleen lezen")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices)
    # "Verdeelt post": this member gets the letters no account or department covers.
    sorts_post = models.BooleanField(default=False)

    # Each role may do everything the roles below it may do.
    RANK: ClassVar[dict[str, int]] = {Role.VIEWER: 0, Role.MEMBER: 1, Role.ADMIN: 2}

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["user", "workspace"], name="workspaces_membership_user_workspace_unique"
            ),
        ]

    def has_role(self, min_role: str) -> bool:
        return self.RANK[self.role] >= self.RANK[min_role]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.workspace_id} as {self.role}"


class WorkspaceKey(WorkspaceModel):
    """The workspace's data key, wrapped with the master key (see `apps.workspaces.keys`)."""

    wrapped_key = models.BinaryField()

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["workspace"], name="workspaces_workspacekey_unique"),
        ]

    def __str__(self) -> str:
        return f"key of {self.workspace_id}"


class Invitation(BaseModel):
    """A one-time link with which somebody joins a workspace (D 6.2).

    A server table like `PairingCode`, not a workspace table: it is looked up by its
    token hash before anybody is signed in, so it cannot be under row-level security. It
    holds no workspace content; the person it names is read inside the workspace's
    context only. Only the SHA-256 of the token is stored: the token is in the link's
    fragment and is shown once.
    """

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="invitations")
    # Fixed when the invitation is made; the invitee cannot choose another.
    role = models.CharField(
        max_length=10, choices=Membership.Role.choices, default=Membership.Role.MEMBER
    )
    # "This is Papa": the person the new member is linked to (one without an account link).
    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # When set (lower case), only an account with this address can accept.
    email = models.EmailField(max_length=254, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # The name of who made it, as it was then (shown to the invitee).
    created_by_name = models.CharField(max_length=150)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    # The admin's own name for it ("Anne"), BSN-masked; never shown to the invitee.
    note = models.CharField(max_length=100, blank=True)

    def __str__(self) -> str:
        return f"Invitation to {self.workspace_id}"

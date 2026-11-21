from collections.abc import Iterable
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.db.models.base import ModelBase
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.common.models import WorkspaceModel
from apps.workspaces.models import Membership


class Person(WorkspaceModel):
    """Someone letters are addressed to; optionally a member of the workspace.

    A `department` (an "afdeling" or "groep") receives post like a person, never has an
    account of its own, and its members are `DepartmentMember` rows.
    """

    class Kind(models.TextChoices):
        PERSON = "person", _("Persoon")
        DEPARTMENT = "department", _("Afdeling")

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
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.PERSON)
    # When the account of this person was switched off (the member removed or gone):
    # "Uitgeschakeld" on Mensen. Display only, like `former_user`.
    deactivated_at = models.DateTimeField(null=True, blank=True)
    # The account this person had before it was switched off: its address and last
    # sign-in are shown, and "Opnieuw uitnodigen" starts from that address. Display only
    # (spec M4): no visibility, lock or permission reads it.
    former_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    # The former account's address and last activity, frozen at the moment it was switched
    # off: never read live, so an admin does not follow the account elsewhere (security
    # review of PR #43, point 2). Display only, like `former_user`.
    former_email = models.EmailField(max_length=254, blank=True, default="")
    former_last_sign_in_at = models.DateTimeField(null=True, blank=True)
    former_last_app_use_at = models.DateTimeField(null=True, blank=True)
    # "Beheerd door" (Mensen slice C): the member who reads this person's post as their own
    # while the person has no active account. Set only by `people.services.set_manager`
    # (preview and confirmation); activating the person or removing the member ends it.
    managed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

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
            models.CheckConstraint(
                condition=models.Q(former_user__isnull=True)
                | models.Q(deactivated_at__isnull=False),
                name="people_person_former_user_needs_deactivated",
            ),
            models.CheckConstraint(
                condition=models.Q(managed_by__isnull=True) | models.Q(user__isnull=True),
                name="people_person_managed_only_without_account",
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
        if self.kind == self.Kind.DEPARTMENT and self.user_id is not None:
            raise ValueError("A department has no account of its own.")
        if self.kind == self.Kind.DEPARTMENT and (
            self.deactivated_at is not None or self.former_user_id is not None
        ):
            raise ValueError("A department has no account to switch off.")
        if self.former_user_id is not None and self.deactivated_at is None:
            raise ValueError("A former account needs the moment it was switched off.")
        if self.managed_by_id is not None and (
            self.kind == self.Kind.DEPARTMENT or self.user_id is not None
        ):
            raise ValueError("Only a person without an account is managed by a member.")
        if (
            self.managed_by_id is not None
            and not Membership.objects.filter(
                workspace_id=self.workspace_id, user_id=self.managed_by_id
            ).exists()
        ):
            raise ValueError("A person's manager must be a member of the workspace.")
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


class DepartmentMember(WorkspaceModel):
    """A member of the workspace in a department; a lead ("leidinggevende") manages it."""

    same_workspace_fields = ("department",)

    department = models.ForeignKey(
        Person, on_delete=models.CASCADE, related_name="department_members"
    )
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    is_lead = models.BooleanField(default=False)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["department", "user"], name="people_departmentmember_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.department_id}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if self.department.kind != Person.Kind.DEPARTMENT:
            raise ValueError("Only a department has members.")
        if not Membership.objects.filter(
            workspace_id=self.workspace_id, user_id=self.user_id
        ).exists():
            raise ValueError("A department member must be a member of the workspace.")
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

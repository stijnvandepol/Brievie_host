from collections.abc import Sequence

from django.db import transaction
from django.db.models import Count

from apps.accounts.authority import account_action_requires_server_admin
from apps.accounts.models import User
from apps.labels.presets import apply_preset
from apps.labels.services import create_extra_labels
from apps.people.models import Person
from apps.people.services import ensure_person_for_member
from apps.workspaces.context import workspace_context
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.schemas import MemberOut


def create_workspace(
    *, name: str, kind: str, admin: User, extra_labels: Sequence[str] = ()
) -> Workspace:
    """The workspace with its admin, the start set of labels and `extra_labels` as own labels."""
    if kind not in Workspace.Kind.values:
        raise ValueError(f"Unknown workspace kind: {kind!r}")
    with transaction.atomic():
        workspace = Workspace.objects.create(name=name, kind=kind)
        Membership.objects.create(user=admin, workspace=workspace, role=Membership.Role.ADMIN)
        with workspace_context(workspace.id):
            workspace_key(workspace.id)
            ensure_person_for_member(workspace=workspace, user=admin)
            apply_preset(workspace)
            create_extra_labels(workspace=workspace, names=extra_labels)
    return workspace


def list_memberships(user: User) -> list[Membership]:
    return list(
        Membership.objects.filter(user=user)
        .select_related("workspace")
        .order_by("workspace__name", "workspace__id")
    )


def list_members(workspace: Workspace, *, viewer: User) -> list[MemberOut]:
    """The workspace's members by name, each with their linked person (needs the RLS context)
    and whether `viewer` may act on their account: the rule of `account_authority` (a server
    admin for anyone else; a workspace admin only for a member of this workspace alone who
    is no server admin; nobody for their own account)."""
    person_ids = dict(
        Person.objects.filter(workspace=workspace).exclude(user=None).values_list("user_id", "id")
    )
    memberships = list(
        Membership.objects.filter(workspace=workspace)
        .select_related("user")
        .order_by("user__name", "user__id")
    )
    viewer_administers = viewer.is_active and (
        Membership.objects.filter(
            user=viewer, workspace=workspace, role=Membership.Role.ADMIN
        ).exists()
    )
    counts = dict(
        Membership.objects.filter(user_id__in=[m.user_id for m in memberships])
        .values("user_id")
        .annotate(total=Count("id"))
        .values_list("user_id", "total")
    )

    def may_manage(member: Membership) -> bool:
        target = member.user
        if target.id == viewer.id or not viewer.is_active:
            return False
        if viewer.is_server_admin:
            return True
        return bool(
            viewer_administers
            and counts.get(target.id) == 1
            and not target.is_server_admin
            and not account_action_requires_server_admin(target)
        )

    return [
        MemberOut(
            user_id=m.user_id,
            name=m.user.name,
            role=m.role,
            person_id=person_ids.get(m.user_id),
            can_manage_account=may_manage(m),
        )
        for m in memberships
    ]

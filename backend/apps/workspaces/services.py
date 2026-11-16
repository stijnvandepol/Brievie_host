from django.db import transaction

from apps.accounts.models import User
from apps.people.models import Person
from apps.people.services import ensure_person_for_member
from apps.workspaces.context import workspace_context
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.schemas import MemberOut


def create_workspace(*, name: str, kind: str, admin: User) -> Workspace:
    if kind not in Workspace.Kind.values:
        raise ValueError(f"Unknown workspace kind: {kind!r}")
    with transaction.atomic():
        workspace = Workspace.objects.create(name=name, kind=kind)
        Membership.objects.create(user=admin, workspace=workspace, role=Membership.Role.ADMIN)
        with workspace_context(workspace.id):
            workspace_key(workspace.id)
            ensure_person_for_member(workspace=workspace, user=admin)
    return workspace


def list_memberships(user: User) -> list[Membership]:
    return list(
        Membership.objects.filter(user=user)
        .select_related("workspace")
        .order_by("workspace__name", "workspace__id")
    )


def list_members(workspace: Workspace) -> list[MemberOut]:
    """The workspace's members by name, each with their linked person (needs the RLS context)."""
    person_ids = dict(
        Person.objects.filter(workspace=workspace).exclude(user=None).values_list("user_id", "id")
    )
    memberships = (
        Membership.objects.filter(workspace=workspace)
        .select_related("user")
        .order_by("user__name", "user__id")
    )
    return [
        MemberOut(
            user_id=m.user_id, name=m.user.name, role=m.role, person_id=person_ids.get(m.user_id)
        )
        for m in memberships
    ]

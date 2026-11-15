from django.db import transaction

from apps.accounts.models import User
from apps.workspaces.context import workspace_context
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Membership, Workspace


def create_workspace(*, name: str, kind: str, admin: User) -> Workspace:
    if kind not in Workspace.Kind.values:
        raise ValueError(f"Unknown workspace kind: {kind!r}")
    with transaction.atomic():
        workspace = Workspace.objects.create(name=name, kind=kind)
        Membership.objects.create(user=admin, workspace=workspace, role=Membership.Role.ADMIN)
        with workspace_context(workspace.id):
            workspace_key(workspace.id)
    return workspace


def list_memberships(user: User) -> list[Membership]:
    return list(
        Membership.objects.filter(user=user)
        .select_related("workspace")
        .order_by("workspace__name", "workspace__id")
    )

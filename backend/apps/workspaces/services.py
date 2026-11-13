from django.db import transaction

from apps.accounts.models import User
from apps.workspaces.models import Membership, Workspace


def create_workspace(*, name: str, kind: str, admin: User) -> Workspace:
    if kind not in Workspace.Kind.values:
        raise ValueError(f"Unknown workspace kind: {kind!r}")
    with transaction.atomic():
        workspace = Workspace.objects.create(name=name, kind=kind)
        Membership.objects.create(user=admin, workspace=workspace, role=Membership.Role.ADMIN)
    return workspace

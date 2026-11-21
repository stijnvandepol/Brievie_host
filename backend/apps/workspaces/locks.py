"""The first lock of every request that organises a letter (PB T6, plan Task 5).

One lock order: the caller's own membership (`FOR SHARE`), then documents and persons
(`FOR NO KEY UPDATE`). A membership change (`FOR UPDATE` on the workspace's memberships)
then waits for the organising request, or the request finds the member gone, and the two
never hold each other's rows. A leaf module: it imports nothing of the apps but errors
and the workspace models.
"""

from uuid import UUID

from django.db import connection
from django.utils.translation import gettext as _

from apps.common.errors import ApiError
from apps.workspaces.models import Membership, Workspace


def lock_own_membership(workspace_id: UUID, user_id: UUID) -> None:
    """Hold the caller's membership row until the transaction ends; 404
    `workspace_not_found` when it is gone (as `workspace_member` answers)."""
    with connection.cursor() as c:
        c.execute(
            "SELECT id FROM workspaces_membership"
            " WHERE workspace_id = %s AND user_id = %s FOR SHARE",
            [workspace_id, user_id],
        )
        row = c.fetchone()
    if row is None:
        raise ApiError(404, "workspace_not_found", _("We kunnen deze werkruimte niet vinden."))


def lock_memberships(workspace: Workspace) -> list[Membership]:
    """Lock every membership of the workspace, in id order: the one lock order of every
    member change and of every change of who sees what. Must run inside a transaction."""
    return list(Membership.objects.select_for_update().filter(workspace=workspace).order_by("id"))

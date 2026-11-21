"""Who may act on somebody else's account (D 6.3, D 20, D 21).

An account belongs to the whole server, not to a workspace. So an account action (a
reset link, ending sessions, seeing or revoking devices) on another person's account is
for a server admin, or for a workspace admin when that workspace is the person's only
membership and the person is no server admin. Every such action asks `account_authority`
first, and a reset asks again when it is completed.
"""

from dataclasses import dataclass
from uuid import UUID

from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.workspaces.models import Membership, Workspace


@dataclass(frozen=True)
class AccountAuthority:
    target: User
    # The workspace whose log also records the action: the target's only workspace.
    workspace_id: UUID | None


def actor_reads_all_post(workspace: Workspace, *, actor_is_admin: bool) -> bool:
    """Condition 3 of S 4.19: the actor reads all post of the workspace that is not
    private ("Iedereen ziet alle post", or "Beheerders lezen alle post" for an admin)."""
    return workspace.everyone_sees_all or (workspace.admins_read_all and actor_is_admin)


def account_action_requires_server_admin(target: User, *, actor: User) -> bool:
    """Whether only a server admin may act on `target`'s account (S 4.19, the owner's
    decision of 2026-11-19, DECISIONS "6. Reset links"). A workspace admin may only when
    (1) the target's single membership is that workspace and the target is no server
    admin, (2) the target reads no private letter there (emergency access does not
    count), and (3) the actor reads all post there that is not private. An account
    action lets the actor act as the member, so it may not reach further than the
    actor's own sight. `list_members` applies the same three conditions to a list."""
    from apps.workspaces import access  # access imports the apps that import this module

    memberships = list(Membership.objects.filter(user_id=target.id).select_related("workspace"))
    if len(memberships) != 1 or target.is_server_admin:
        return True
    workspace = memberships[0].workspace
    actor_is_admin = Membership.objects.filter(
        user_id=actor.id, workspace=workspace, role=Membership.Role.ADMIN
    ).exists()
    if not actor_reads_all_post(workspace, actor_is_admin=actor_is_admin):
        return True
    return access.reads_private_post(workspace.id, target.id)


def _not_found() -> ApiError:
    return ApiError(404, "user_not_found", _("We kunnen dit account niet vinden."))


def account_authority(actor: User, target_id: UUID) -> AccountAuthority:
    """The account `actor` may act on, or an `ApiError`.

    404 `user_not_found` when the account does not exist or the actor is no admin of any
    of its workspaces (the two answer alike, with the same queries, so an id reveals
    nothing); 403 `server_admin_only` when the actor is an admin of its workspace, but the
    account also exists elsewhere (another membership, a server admin); 403 `own_account`
    for the actor's own account, which has its own pages (where the current password is
    asked).
    """
    target = User.objects.filter(id=target_id).first()
    target_workspaces = set(
        Membership.objects.filter(user_id=target_id).values_list("workspace_id", flat=True)
    )
    administered = set(
        Membership.objects.filter(user_id=actor.id, role=Membership.Role.ADMIN).values_list(
            "workspace_id", flat=True
        )
    )
    if target is None or not actor.is_active:
        raise _not_found()
    if target.id == actor.id:
        raise ApiError(
            403, "own_account", _("Je eigen account regel je bij je eigen instellingen.")
        )
    only_workspace = next(iter(target_workspaces)) if len(target_workspaces) == 1 else None
    if actor.is_server_admin:
        return AccountAuthority(target=target, workspace_id=only_workspace)
    if not target_workspaces & administered:
        raise _not_found()
    if only_workspace is None or target.is_server_admin:
        raise ApiError(
            403,
            "server_admin_only",
            _(
                "Dit kan alleen de serverbeheerder, omdat dit account ook buiten deze "
                "werkruimte bestaat."
            ),
        )
    if account_action_requires_server_admin(target, actor=actor):
        raise ApiError(403, "server_admin_only", _("Dit kan alleen de serverbeheerder."))
    return AccountAuthority(target=target, workspace_id=only_workspace)

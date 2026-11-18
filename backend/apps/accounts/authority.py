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
from apps.workspaces.models import Membership


@dataclass(frozen=True)
class AccountAuthority:
    target: User
    # The workspace whose log also records the action: the target's only workspace.
    workspace_id: UUID | None


def account_action_requires_server_admin(target: User) -> bool:
    """Seam for private post (a later part, provisional ruling D 8.5): True when only a
    server admin may act on `target`'s account, also for a single-membership member.
    Nothing in this part makes it True."""
    return False


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
    if (
        only_workspace is None
        or target.is_server_admin
        or account_action_requires_server_admin(target)
    ):
        raise ApiError(
            403,
            "server_admin_only",
            _(
                "Dit kan alleen de serverbeheerder, omdat dit account ook buiten deze "
                "werkruimte bestaat."
            ),
        )
    return AccountAuthority(target=target, workspace_id=only_workspace)

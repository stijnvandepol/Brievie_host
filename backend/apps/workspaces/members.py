"""Member changes (D 6.3): a member's role, removing a member and leaving.

Every change takes the same lock first: the workspace's memberships, in id order. The
caller's own role is then read again from the locked rows, so two admins acting on each
other at the same moment run one after the other, and the second one acts with the role
it has after the first. A workspace always keeps an admin.
"""

from uuid import UUID

from django.db import transaction
from django.utils.translation import gettext as _

from apps.accounts.resets import revoke_resets_made_by
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.people.models import Person
from apps.workspaces.context import workspace_context
from apps.workspaces.invitations import revoke_invitations_made_by
from apps.workspaces.models import Membership, Workspace


def lock_memberships(workspace: Workspace) -> list[Membership]:
    """Lock every membership of the workspace, in id order: the one lock order of every
    member change. Must run inside a transaction."""
    return list(Membership.objects.select_for_update().filter(workspace=workspace).order_by("id"))


def _forbidden() -> ApiError:
    return ApiError(403, "forbidden", _("Je mag dit niet doen in deze werkruimte."))


def _member_not_found() -> ApiError:
    return ApiError(404, "member_not_found", _("We kunnen dit lid niet vinden."))


def _last_admin() -> ApiError:
    return ApiError(
        409,
        "last_admin",
        _(
            "Een werkruimte heeft altijd minstens één beheerder. "
            "Maak eerst iemand anders beheerder."
        ),
    )


def _is_only_admin(member: Membership, locked: list[Membership]) -> bool:
    admins = [m for m in locked if m.role == Membership.Role.ADMIN]
    return member.role == Membership.Role.ADMIN and len(admins) == 1


def _require_admin(locked: list[Membership], actor: AuditActor) -> None:
    """The actor must be an admin by the locked rows (not by what the endpoint saw)."""
    own = next((m for m in locked if m.user_id == actor.user_id), None)
    if own is None or own.role != Membership.Role.ADMIN:
        raise _forbidden()


def _member_target(workspace: Workspace, member: Membership) -> AuditTarget:
    """The member as audit target, named after their person (needs the workspace's
    context); the account's name when no person is linked."""
    with workspace_context(workspace.id):
        name = (
            Person.objects.filter(workspace=workspace, user_id=member.user_id)
            .values_list("display_name", flat=True)
            .first()
        )
    return AuditTarget(kind=TargetKind.USER, id=member.user_id, name=name or member.user.name)


def _revoke_what_the_admin_made(workspace: Workspace, user_id: UUID, actor: AuditActor) -> None:
    """What an admin made stops when they stop being admin (D 21)."""
    revoke_invitations_made_by(workspace_id=workspace.id, user_id=user_id, actor=actor)
    revoke_resets_made_by(user_id)


def change_role(*, workspace: Workspace, user_id: UUID, role: str, actor: AuditActor) -> Membership:
    """Give a member of `workspace` another role; the actor is its admin.

    403 `forbidden` when the actor is no admin by the locked rows; 404 `member_not_found`
    for somebody who is no member of this workspace; 409 `last_admin` for demoting the
    only admin. The same role writes nothing. A demoted admin's open invitations and
    reset links stop. The member's sessions keep working, with the new role at their
    next request.
    """
    if role not in Membership.Role.values:
        raise ApiError(422, "validation_error", _("Kies een rol: beheerder, lid of meekijker."))
    with transaction.atomic():
        locked = lock_memberships(workspace)
        _require_admin(locked, actor)
        member = next((m for m in locked if m.user_id == user_id), None)
        if member is None:
            raise _member_not_found()
        if member.role == role:
            return member
        if role != Membership.Role.ADMIN and _is_only_admin(member, locked):
            raise _last_admin()
        previous = member.role
        member.role = role
        member.save(update_fields=["role", "updated_at"])
        if previous == Membership.Role.ADMIN:
            _revoke_what_the_admin_made(workspace, user_id, actor)
        record(
            AuditCode.MEMBER_ROLE_CHANGED,
            actor=actor,
            target=_member_target(workspace, member),
            workspace_id=workspace.id,
            values={"from": previous, "to": role},
        )
    return member

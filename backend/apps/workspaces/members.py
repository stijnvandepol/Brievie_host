"""Member changes (D 6.3): a member's role, removing a member and leaving.

Every change takes the same lock first: the workspace's memberships, in id order. The
caller's own role is then read again from the locked rows, so two admins acting on each
other at the same moment run one after the other, and the second one acts with the role
it has after the first. A workspace always keeps an admin.

Lock order of every member change, and of everything that meets it:
memberships (of one workspace, id order) -> invitations -> active server admins (id
order, `FOR NO KEY UPDATE`) -> the member's own user row (`FOR NO KEY UPDATE`) -> that
user's sessions, devices and reset links. Accepting an invitation takes invitation ->
user; completing a reset takes user -> reset; `set_server_admin` takes server admins ->
user. No path takes them the other way round.
"""

from uuid import UUID

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.account_state import deactivate_account, last_server_admin_error
from apps.accounts.models import User
from apps.accounts.resets import revoke_resets_made_by
from apps.accounts.services import lock_active_server_admins
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.documents.member_export import remove_member_exports
from apps.people.models import Person
from apps.tasks.models import Task
from apps.workspaces.access import on_member_removed
from apps.workspaces.context import workspace_context
from apps.workspaces.invitations import revoke_invitations_bound_to, revoke_invitations_made_by
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


def _server_admin_only() -> ApiError:
    return ApiError(
        403,
        "server_admin_only",
        _(
            "Dit account is serverbeheerder. Alleen een serverbeheerder kan het uit zijn "
            "laatste werkruimte halen."
        ),
    )


def _unlink_and_unassign(workspace: Workspace, user_id: UUID) -> list[Person]:
    """Inside the workspace's context: the member's person stays without its account
    link, their tasks here lose their assignee, and part B's seam runs. Returns the
    unlinked persons. A queryset `update()`, before the membership goes, because
    `Person.save` refuses a user who is no member."""
    now = timezone.now()
    with workspace_context(workspace.id):
        persons = list(Person.objects.filter(workspace=workspace, user_id=user_id).order_by("id"))
        Person.objects.filter(id__in=[p.id for p in persons]).update(user=None, updated_at=now)
        Task.objects.filter(workspace=workspace, assigned_to_id=user_id).update(
            assigned_to=None, updated_at=now
        )
        on_member_removed(workspace.id, user_id)
        # Their own export goes with them (its files once this transaction commits).
        remove_member_exports(workspace.id, user_id)
    return persons


def remove_member(
    *, request: HttpRequest, workspace: Workspace, user_id: UUID, actor: AuditActor, left: bool
) -> None:
    """Remove a member from `workspace` (an admin's act), or let the caller leave it
    (`left`): one transaction, so a failure anywhere leaves everything as it was.

    403 `forbidden` when a removing actor is no admin by the locked rows; 404
    `member_not_found`; 409 `last_admin` for the only admin (also leaving or removing
    themselves). When this is the user's last membership the account is switched off
    (`deactivate_account`): only a server admin may do that to a server admin (403
    `server_admin_only`), and never to the last active one (409 `last_admin`).

    Locks in the order at the top of this module. Open invitations bound to the
    member's address stop too, so an old link cannot bring them back. The user's row
    lock (`FOR NO KEY UPDATE`, so a running upload that references the user does not
    hold it up) makes two removals of the same user from two workspaces at once run one
    after the other, so the second sees whether a membership is left.
    """
    with transaction.atomic():
        locked = lock_memberships(workspace)
        if not left:
            _require_admin(locked, actor)
        member = next((m for m in locked if m.user_id == user_id), None)
        if member is None:
            raise _member_not_found()
        if _is_only_admin(member, locked):
            raise _last_admin()
        # Invitations before the server admins and the user (see the lock order above).
        if member.role == Membership.Role.ADMIN:
            _revoke_what_the_admin_made(workspace, user_id, actor)
        revoke_invitations_bound_to(
            workspace_id=workspace.id,
            email=member.user.email,
            actor=actor,
            reason="member_removed",
        )
        server_admins = lock_active_server_admins()
        user = User.objects.select_for_update(no_key=True).get(id=user_id)
        last_membership = not (
            Membership.objects.filter(user_id=user_id).exclude(workspace=workspace).exists()
        )
        if last_membership and user.is_server_admin:
            if all(admin.id != actor.user_id for admin in server_admins):
                raise _server_admin_only()
            if not any(admin.id != user.id for admin in server_admins):
                raise last_server_admin_error()
        target = _member_target(workspace, member)
        persons = _unlink_and_unassign(workspace, user_id)
        member.delete()
        if last_membership:
            deactivate_account(user, actor=actor, current=request)
        for person in persons:
            record(
                AuditCode.PERSON_UNLINKED,
                actor=actor,
                target=AuditTarget(kind=TargetKind.PERSON, id=person.id, name=person.display_name),
                workspace_id=workspace.id,
            )
        record(
            AuditCode.MEMBER_LEFT if left else AuditCode.MEMBER_REMOVED,
            actor=actor,
            target=target,
            workspace_id=workspace.id,
            values={"account_deactivated": last_membership},
        )

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
from django.db.models import Max
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
from apps.devices.models import Device
from apps.documents.member_export import remove_member_exports
from apps.people.models import Person
from apps.tasks.assignment import release_member
from apps.workspaces.access import on_member_removed
from apps.workspaces.context import NOBODY, Actor, workspace_context
from apps.workspaces.invitations import (
    revoke_invitations_bound_to,
    revoke_invitations_for_persons,
    revoke_invitations_made_by,
)
from apps.workspaces.locks import lock_memberships as lock_memberships
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.visibility_rules import UNASSIGNED
from apps.workspaces.visibility_settings import (
    MemberChange,
    member_change_key,
    notify_self_grant,
    sight_change,
)


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
    with workspace_context(workspace.id, actor=NOBODY):
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


def change_role(
    *,
    workspace: Workspace,
    user_id: UUID,
    role: str,
    actor: AuditActor,
    confirmed: bool = False,
    preview_token: str = "",
) -> Membership:
    """Give a member of `workspace` another role; the actor is its admin.

    403 `forbidden` when the actor is no admin by the locked rows; 404 `member_not_found`
    for somebody who is no member of this workspace; 409 `last_admin` for demoting the
    only admin. The same role writes nothing. A demoted admin's open invitations and
    reset links stop. The member's sessions keep working, with the new role at their
    next request. A viewer sorts no post: the flag goes with the role. When anybody
    gains sight (a new admin while admins read all post, a new fallback sorter), the
    confirmed preview's token is needed (409 `confirmation_required`,
    `preview_outdated`; S 4.4).
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
        with sight_change(
            workspace,
            narrowing=Membership.RANK[role] < Membership.RANK[previous],
            confirmed=confirmed,
            preview_token=preview_token,
            actor=actor,
            change=member_change_key(MemberChange(kind="role", user_id=user_id, role=role)),
        ):
            member.role = role
            stops_sorting = role == Membership.Role.VIEWER and member.sorts_post
            if stops_sorting:
                member.sorts_post = False
            member.save(
                update_fields=["role", "updated_at", *(["sorts_post"] if stops_sorting else [])]
            )
            if role == Membership.Role.VIEWER:
                # A viewer is given no task (S 5.3): their assignments end with the role.
                as_actor: Actor = actor.user_id if actor.user_id is not None else NOBODY
                with workspace_context(workspace.id, actor=as_actor):
                    release_member(workspace_id=workspace.id, user_id=user_id)
            if previous == Membership.Role.ADMIN:
                _revoke_what_the_admin_made(workspace, user_id, actor)
            target = _member_target(workspace, member)
            record(
                AuditCode.MEMBER_ROLE_CHANGED,
                actor=actor,
                target=target,
                workspace_id=workspace.id,
                values={"from": previous, "to": role},
            )
            if stops_sorting:
                record(
                    AuditCode.POST_SORTER_CHANGED,
                    actor=actor,
                    target=target,
                    workspace_id=workspace.id,
                    values={"member": target.name, "value": False},
                )
    return member


def _viewer_cannot_sort() -> ApiError:
    return ApiError(422, "viewer_cannot_sort", _("Iemand met Alleen lezen kan geen post verdelen."))


def set_sorts_post(
    *,
    workspace: Workspace,
    user_id: UUID,
    sorts_post: bool,
    actor: AuditActor,
    confirmed: bool = False,
    preview_token: str = "",
) -> Membership:
    """Flag a member as one who sorts post ("Verdeelt post"), or not (S 4.1 rule 4).

    403 `forbidden` for a non-admin by the locked rows, 404 `member_not_found`, 422
    `viewer_cannot_sort`; flagging needs the confirmed preview (the member gains the post
    nobody reads), unflagging only when admins become the sorters and gain by it. The
    same value writes nothing. An admin who flags themselves: the other admins are told
    (S 4.7)."""
    with transaction.atomic():
        locked = lock_memberships(workspace)
        _require_admin(locked, actor)
        member = next((m for m in locked if m.user_id == user_id), None)
        if member is None:
            raise _member_not_found()
        if member.sorts_post == sorts_post:
            return member
        if sorts_post and member.role == Membership.Role.VIEWER:
            raise _viewer_cannot_sort()
        with sight_change(
            workspace,
            narrowing=not sorts_post,
            confirmed=confirmed,
            preview_token=preview_token,
            actor=actor,
            change=member_change_key(
                MemberChange(kind="sorts_post", user_id=user_id, sorts_post=sorts_post)
            ),
        ) as sight:
            member.sorts_post = sorts_post
            member.save(update_fields=["sorts_post", "updated_at"])
            target = _member_target(workspace, member)
            record(
                AuditCode.POST_SORTER_CHANGED,
                actor=actor,
                target=target,
                workspace_id=workspace.id,
                values={"member": target.name, "value": sorts_post},
            )
        if sorts_post and actor.user_id == user_id:
            others = [
                m.user for m in locked if m.role == Membership.Role.ADMIN and m.user_id != user_id
            ]
            notify_self_grant(
                others, category=UNASSIGNED, names=sight.before, actor_name=actor.name
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


def _unlink_and_unassign(
    workspace: Workspace, user_id: UUID, actor: AuditActor
) -> tuple[list[Person], list[Person]]:
    """Inside the workspace's context: the member's person stays without its account
    link, marked "uitgeschakeld" (`deactivated_at`, `former_user`: display only, spec
    M4), their tasks here lose their assignee, and part B's seam runs. Returns the
    unlinked persons and the persons they managed ("Beheerd door" ends with them: P7,
    that post goes back to the sorters). A queryset `update()`, before the membership
    goes, because `Person.save` refuses a user who is no member; one statement, so the
    check "former_user needs deactivated_at" holds at every moment."""
    now = timezone.now()
    as_actor: Actor = actor.user_id if actor.user_id is not None else NOBODY
    account = User.objects.filter(id=user_id).values_list("email", "last_login").first()
    email, last_login = account if account is not None else ("", None)
    app_use = Device.objects.filter(user_id=user_id, revoked_at__isnull=True).aggregate(
        seen=Max("last_seen_at")
    )["seen"]
    with workspace_context(workspace.id, actor=as_actor):
        persons = list(Person.objects.filter(workspace=workspace, user_id=user_id).order_by("id"))
        # The account as it is now, frozen: the list never reads it live afterwards.
        Person.objects.filter(id__in=[p.id for p in persons]).update(
            user=None,
            deactivated_at=now,
            former_user_id=user_id,
            former_email=email,
            former_last_sign_in_at=last_login,
            former_last_app_use_at=app_use,
            updated_at=now,
        )
        managed = list(
            Person.objects.filter(workspace=workspace, managed_by_id=user_id).order_by("id")
        )
        Person.objects.filter(id__in=[p.id for p in managed]).update(
            managed_by=None, updated_at=now
        )
        release_member(workspace_id=workspace.id, user_id=user_id)
        on_member_removed(workspace.id, user_id)
        # Their own export goes with them (its files once this transaction commits).
        remove_member_exports(workspace.id, user_id)
    return persons, managed


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
        # And every link for the member's person, whatever its address: a switched-off
        # person comes back only by a new invitation (security review of PR #43, point 1).
        with workspace_context(workspace.id, actor=NOBODY):
            own_persons = list(
                Person.objects.filter(workspace=workspace, user_id=user_id).values_list(
                    "id", flat=True
                )
            )
        revoke_invitations_for_persons(
            workspace_id=workspace.id, person_ids=own_persons, actor=actor, reason="member_removed"
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
        # S 4.4: never a confirmation for a removal; the sorters who gain are told.
        with sight_change(
            workspace,
            narrowing=True,
            confirmed=False,
            preview_token="",
            actor=actor,
            change=member_change_key(MemberChange(kind="remove", user_id=user_id)),
            ask_confirmation=False,
        ):
            persons, managed = _unlink_and_unassign(workspace, user_id, actor)
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
        for person in managed:
            record(
                AuditCode.PERSON_MANAGER_CLEARED,
                actor=actor,
                target=AuditTarget(kind=TargetKind.PERSON, id=person.id, name=person.display_name),
                workspace_id=workspace.id,
                values={"member": target.name},
            )
        record(
            AuditCode.MEMBER_LEFT if left else AuditCode.MEMBER_REMOVED,
            actor=actor,
            target=target,
            workspace_id=workspace.id,
            values={"account_deactivated": last_membership},
        )


def removal_preview_step(*, workspace: Workspace, user_id: UUID, actor: AuditActor) -> None:
    """The part of `remove_member` that moves sight, for `previewVisibilityChange` only
    (inside its savepoint, rolled back): the same refusals (403, 404, 409 `last_admin`),
    the person unlinked, the member's rows forgotten, the membership gone. The account
    itself is left alone."""
    locked = lock_memberships(workspace)
    _require_admin(locked, actor)
    member = next((m for m in locked if m.user_id == user_id), None)
    if member is None:
        raise _member_not_found()
    if _is_only_admin(member, locked):
        raise _last_admin()
    _unlink_and_unassign(workspace, user_id, actor)
    member.delete()

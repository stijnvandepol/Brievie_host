"""Invitations (D 6.2): an admin makes a one-time link with which somebody joins.

The token is shown once, in the link's fragment; only its hash is stored. The role, the
person and the email address are fixed when the link is made; the invitee chooses none
of them. A link works once, until it expires or is revoked, and only while its maker is
still an admin of the workspace; every link that does not work answers one sentence.
Accepting signs the invitee in through `start_session`, as every sign-in does.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.accounts.models import User, UserSession, normalise_email
from apps.accounts.services import build_user, verify_password
from apps.accounts.sessions import start_session
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.links import fragment_link, home_network_only
from apps.common.net import valid_client_ip
from apps.common.privacy import mask_bsns
from apps.common.tokens import new_token, token_hash
from apps.people.models import Person
from apps.people.services import ensure_person_for_member
from apps.pipeline import settings_service
from apps.pipeline.settings_service import AiStatus
from apps.workspaces.access import on_member_added, person_requires_email
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Invitation, Membership, Workspace

INVITATION_PATH = "/uitnodiging"
VALIDITY_DAYS = (1, 7, 30)
NOTE_MAX = 100
NEW_ACCOUNT = "new"
EXISTING_ACCOUNT = "existing"
_MEMBERSHIP_UNIQUE = "workspaces_membership_user_workspace_unique"
_PERSON_USER_UNIQUE = "people_person_user_unique"


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: Invitation
    link: str
    home_network_only: bool


def _target(invitation: Invitation) -> AuditTarget:
    # The admin's note or the address, never the token.
    return AuditTarget(
        kind=TargetKind.INVITATION, id=invitation.id, name=invitation.note or invitation.email
    )


def _clean_email(email: str) -> str:
    email = normalise_email(email)
    if email:
        validate_email(email)
    return email


def _invitable_person(workspace: Workspace, person_id: UUID) -> Person:
    """The person, locked; needs the workspace's context. Another workspace's person and
    an archived one answer as an unknown one."""
    person = (
        Person.objects.select_for_update()
        .filter(workspace=workspace, id=person_id, archived_at__isnull=True)
        .first()
    )
    if person is None:
        raise ApiError(404, "person_not_found", _("We kunnen deze persoon niet vinden."))
    if person.user_id is not None:
        raise ApiError(409, "person_already_linked", _("Deze persoon is al gekoppeld aan een lid."))
    return person


def _issue(
    *,
    workspace: Workspace,
    creator: User,
    actor: AuditActor,
    request: HttpRequest,
    role: str,
    expires_in_days: int,
    email: str,
    person_id: UUID | None,
    note: str,
) -> IssuedInvitation:
    """Check the choices, store the hash and record it; runs inside the caller's
    transaction and the workspace's context."""
    if role not in Membership.Role.values:
        raise ApiError(422, "validation_error", _("Kies een rol: beheerder, lid of meekijker."))
    if expires_in_days not in VALIDITY_DAYS:
        raise ApiError(422, "validation_error", _("Een uitnodiging werkt 1, 7 of 30 dagen."))
    email = _clean_email(email)
    person = _invitable_person(workspace, person_id) if person_id is not None else None
    if person is not None and not email and person_requires_email(person):
        raise ApiError(
            422,
            "email_required",
            _(
                "Vul het e-mailadres van deze persoon in. "
                "Alleen dat adres kan de uitnodiging gebruiken."
            ),
        )
    if email and Membership.objects.filter(workspace=workspace, user__email=email).exists():
        raise ApiError(409, "already_member", _("Iemand met dit e-mailadres is al lid."))
    token, digest = new_token()
    invitation = Invitation.objects.create(
        workspace=workspace,
        role=role,
        person=person,
        email=email,
        token_hash=digest,
        expires_at=timezone.now() + timedelta(days=expires_in_days),
        created_by=creator,
        created_by_name=creator.name,
        note=mask_bsns(note).strip()[:NOTE_MAX],
    )
    record(
        AuditCode.INVITATION_CREATED,
        actor=actor,
        target=_target(invitation),
        workspace_id=workspace.id,
        values={"role": role, "expires_in_days": expires_in_days},
    )
    return IssuedInvitation(
        invitation=invitation,
        link=fragment_link(request, INVITATION_PATH, token),
        home_network_only=home_network_only(request),
    )


def create_invitation(
    *,
    membership: Membership,
    actor: AuditActor,
    request: HttpRequest,
    role: str,
    expires_in_days: int,
    email: str,
    person_id: UUID | None,
    note: str,
) -> IssuedInvitation:
    """Make an invitation to `membership`'s workspace; the caller is its admin (checked
    by the endpoint, which also entered the workspace's context)."""
    with transaction.atomic():
        return _issue(
            workspace=membership.workspace,
            creator=membership.user,
            actor=actor,
            request=request,
            role=role,
            expires_in_days=expires_in_days,
            email=email,
            person_id=person_id,
            note=note,
        )


def list_invitations(workspace: Workspace) -> list[Invitation]:
    """Open invitations, expired ones included, newest first; needs the workspace's
    context (for the person's name)."""
    return list(
        Invitation.objects.filter(workspace=workspace, accepted_at=None, revoked_at=None)
        .select_related("person")
        .order_by("-created_at", "-id")
    )


def _open_for_update(workspace: Workspace, invitation_id: UUID) -> Invitation:
    """An invitation of this workspace that is neither used nor revoked, locked. One of
    another workspace answers as an unknown one."""
    invitation = (
        Invitation.objects.select_for_update()
        .filter(workspace=workspace, id=invitation_id, accepted_at=None, revoked_at=None)
        .first()
    )
    if invitation is None:
        raise ApiError(404, "invitation_not_found", _("We kunnen deze uitnodiging niet vinden."))
    return invitation


def _revoke(invitation: Invitation, *, actor: AuditActor, reason: str) -> None:
    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["revoked_at", "updated_at"])
    record(
        AuditCode.INVITATION_REVOKED,
        actor=actor,
        target=_target(invitation),
        workspace_id=invitation.workspace_id,
        values={"reason": reason},
    )


def revoke_invitation(*, workspace: Workspace, invitation_id: UUID, actor: AuditActor) -> None:
    with transaction.atomic():
        _revoke(_open_for_update(workspace, invitation_id), actor=actor, reason="admin")


def _validity_days(invitation: Invitation) -> int:
    """The validity it was made with (1, 7 or 30 days)."""
    days = round((invitation.expires_at - invitation.created_at) / timedelta(days=1))
    return min(VALIDITY_DAYS, key=lambda choice: abs(choice - days))


def renew_invitation(
    *, membership: Membership, actor: AuditActor, request: HttpRequest, invitation_id: UUID
) -> IssuedInvitation:
    """A new link with the same role, person, email, note and validity; the old link
    stops. The rules of a new invitation apply again (the person may be linked by now)."""
    with transaction.atomic():
        old = _open_for_update(membership.workspace, invitation_id)
        _revoke(old, actor=actor, reason="renewed")
        return _issue(
            workspace=membership.workspace,
            creator=membership.user,
            actor=actor,
            request=request,
            role=old.role,
            expires_in_days=_validity_days(old),
            email=old.email,
            person_id=old.person_id,
            note=old.note,
        )


def revoke_invitations_made_by(*, workspace_id: UUID, user_id: UUID, actor: AuditActor) -> int:
    """Stop every open invitation `user_id` made in this workspace, for when they stop
    being its admin (Tasks 13 and 14). Returns how many."""
    with transaction.atomic():
        invitations = list(
            Invitation.objects.select_for_update()
            .filter(workspace_id=workspace_id, created_by_id=user_id, accepted_at=None)
            .filter(revoked_at=None)
            .order_by("id")
        )
        for invitation in invitations:
            _revoke(invitation, actor=actor, reason="creator_lost_role")
    return len(invitations)


def revoke_invitations_bound_to(
    *,
    workspace_id: UUID,
    email: str,
    actor: AuditActor,
    reason: str,
    keep_id: UUID | None = None,
    skip_locked: bool = False,
) -> int:
    """Stop every open invitation to this workspace that is bound to `email`: when that
    account is removed or leaves (`member_removed`), an old link with its address must
    not bring it back; when it joins (`superseded`, all but `keep_id`), the others have
    done their work. `skip_locked` leaves a link another request is accepting right now
    (that accept then finds the account a member and answers 409). Returns how many."""
    email = normalise_email(email)
    if not email:
        return 0
    with transaction.atomic():
        rows = (
            Invitation.objects.select_for_update(skip_locked=skip_locked)
            .filter(workspace_id=workspace_id, email=email, accepted_at=None, revoked_at=None)
            .order_by("id")
        )
        if keep_id is not None:
            rows = rows.exclude(id=keep_id)
        invitations = list(rows)
        for invitation in invitations:
            _revoke(invitation, actor=actor, reason=reason)
    return len(invitations)


# --- Opening and accepting (no session needed) ---


@dataclass(frozen=True)
class InvitationInfo:
    workspace_name: str
    invited_by: str
    role: str
    email_fixed: bool
    person_name: str
    ai: AiStatus


def _dead_invitation() -> ApiError:
    # One sentence for unknown, used, expired and revoked links, and for a link whose
    # maker is no admin of the workspace any more: a link reveals nothing.
    return ApiError(
        400, "invitation_invalid", _("Deze uitnodiging werkt niet meer. Vraag een nieuwe.")
    )


def _usable(token: str, *, lock: bool = False) -> Invitation:
    """The invitation this token opens, or the one answer for every dead link.

    One query whatever the reason, so a dead link costs as much as an unknown one. The
    role was fixed by its maker, so the maker must still be an active admin of the
    workspace (an admin who lost the role cannot hand it out any more).
    """
    maker_is_admin = Membership.objects.filter(
        workspace_id=OuterRef("workspace_id"),
        user_id=OuterRef("created_by_id"),
        role=Membership.Role.ADMIN,
        user__is_active=True,
    )
    invitations: QuerySet[Invitation] = Invitation.objects.select_related("workspace").filter(
        Exists(maker_is_admin),
        token_hash=token_hash(token),
        accepted_at=None,
        revoked_at=None,
        expires_at__gt=timezone.now(),
    )
    if lock:
        invitations = invitations.select_for_update(of=("self",))
    invitation = invitations.first()
    if invitation is None:
        raise _dead_invitation()
    return invitation


def lookup_invitation(*, token: str) -> InvitationInfo:
    """What the invitee may know before accepting: the workspace's name, who invited, the
    role, whether the address is fixed, the person and the workspace's AI line. The person
    and the AI settings are workspace rows, read inside the workspace's context."""
    invitation = _usable(token)
    with workspace_context(invitation.workspace_id):
        person_name = (
            Person.objects.filter(workspace_id=invitation.workspace_id, id=invitation.person_id)
            .values_list("display_name", flat=True)
            .first()
            if invitation.person_id is not None
            else None
        )
        ai = settings_service.status(invitation.workspace_id)
    return InvitationInfo(
        workspace_name=invitation.workspace.name,
        invited_by=invitation.created_by_name,
        role=invitation.role,
        email_fixed=bool(invitation.email),
        person_name=person_name or "",
        ai=ai,
    )


def _account_exists(email: str) -> bool:
    """Whether an account, active or not, has this address."""
    return User.objects.filter(email=normalise_email(email)).exists()


def _is_member(user: User, workspace_id: UUID) -> bool:
    return Membership.objects.filter(user=user, workspace_id=workspace_id).exists()


def _unique_constraint(exc: IntegrityError) -> str | None:
    cause = exc.__cause__
    if isinstance(cause, pg_errors.UniqueViolation):
        return cause.diag.constraint_name
    return None


def _account_exists_error() -> ApiError:
    return ApiError(
        409,
        "account_exists",
        # The typographic quotes are part of the sentence the plan fixes.
        _("Er is al een account met dit e-mailadres. Kies ‘Ik heb al een account’."),  # noqa: RUF001
    )


def _email_mismatch(invitation: Invitation, email: str) -> bool:
    return bool(invitation.email) and invitation.email != normalise_email(email)


def _email_mismatch_error() -> ApiError:
    return ApiError(
        403, "invitation_email_mismatch", _("Deze uitnodiging is voor een ander e-mailadres.")
    )


def _already_member_error() -> ApiError:
    return ApiError(409, "already_member", _("Je bent al lid van deze werkruimte."))


def accept_invitation(
    request: HttpRequest, *, token: str, account: str, name: str, email: str, password: str
) -> Membership:
    """Join the workspace with a new account or an existing one, and sign in.

    An existing account is found only by its address together with its password (the
    sign-in checks: lockout, counting, log), checked outside the transaction so a
    failure is kept; a deactivated account comes back this way, and only this way. The
    rest is one transaction under a lock on the invitation, then on the existing account:
    two accepts at once give one member, the other gets the dead-link answer, and a
    removal of the account's last membership at the same moment cannot leave a member
    with a switched-off account. A failure anywhere leaves nothing.
    """
    if _email_mismatch(_usable(token), email):
        # Before the account checks: a foreign address answers this, never whether it
        # has an account.
        raise _email_mismatch_error()
    if account not in (NEW_ACCOUNT, EXISTING_ACCOUNT):
        raise ApiError(
            422, "validation_error", _("Kies een nieuw account of een bestaand account.")
        )
    if account == EXISTING_ACCOUNT:
        user = verify_password(request, email=email, password=password)
    else:
        if _account_exists(email):
            raise _account_exists_error()
        user = build_user(email=email, name=name, password=password)
    is_new = account == NEW_ACCOUNT
    with transaction.atomic():
        invitation = _usable(token, lock=True)
        if not is_new:
            # The account as it is now, locked (lock order: invitation, then the user,
            # as a removal locks the user): a removal of its last membership that
            # committed since the password check has switched it off, and the join
            # must switch it on again.
            user = User.objects.select_for_update(no_key=True).get(pk=user.pk)
        if _email_mismatch(invitation, user.email):
            raise _email_mismatch_error()
        if not is_new and _is_member(user, invitation.workspace_id):
            raise _already_member_error()
        reactivated = not is_new and not user.is_active
        membership = _join(invitation, user, is_new=is_new)
        accepter = AuditActor.for_user(user, client_ip=valid_client_ip(request))
        # Other links bound to this address are done with. Skip one another request is
        # accepting at this moment: waiting for it could deadlock (it waits for this
        # account's row); a removal later stops it anyway.
        revoke_invitations_bound_to(
            workspace_id=invitation.workspace_id,
            email=user.email,
            actor=accepter,
            reason="superseded",
            keep_id=invitation.id,
            skip_locked=True,
        )
        with workspace_context(invitation.workspace_id):
            person = _link_person(invitation, user)
            on_member_added(
                invitation.workspace_id,
                user.id,
                person_id=person.id,
                created_by_id=invitation.created_by_id,
            )
        invitation.accepted_at = timezone.now()
        invitation.accepted_by = user
        invitation.save(update_fields=["accepted_at", "accepted_by", "updated_at"])
        record(
            AuditCode.INVITATION_ACCEPTED,
            actor=accepter,
            target=_target(invitation),
            workspace_id=invitation.workspace_id,
            values={
                "role": invitation.role,
                "new_account": is_new,
                "account_reactivated": reactivated,
            },
        )
        start_session(request, user, via=UserSession.Via.INVITATION)
    return membership


def _join(invitation: Invitation, user: User, *, is_new: bool) -> Membership:
    """Save the new account or reactivate the existing one, and make the membership with
    the invitation's role. A concurrent accept that made the same account or membership
    first answers 409, never a server error."""
    try:
        with transaction.atomic():
            if is_new:
                user.save()
            elif not user.is_active:
                user.is_active = True
                user.save(update_fields=["is_active", "updated_at"])
            return Membership.objects.create(
                user=user, workspace=invitation.workspace, role=invitation.role
            )
    except IntegrityError as exc:
        constraint = _unique_constraint(exc)
        if constraint == _MEMBERSHIP_UNIQUE:
            raise _already_member_error() from exc
        if is_new and constraint is not None:
            raise _account_exists_error() from exc
        raise


def _person_taken_error() -> ApiError:
    return ApiError(
        409,
        "invitation_person_taken",
        _("Deze persoon is intussen aan iemand anders gekoppeld. Vraag een nieuwe uitnodiging."),
    )


def _link_person(invitation: Invitation, user: User) -> Person:
    """The invitation's person, now linked to `user` (and logged), or the member's own
    person as for every member. Needs the workspace's context."""
    if invitation.person_id is None:
        return ensure_person_for_member(workspace=invitation.workspace, user=user)
    person = (
        Person.objects.select_for_update()
        .filter(
            workspace_id=invitation.workspace_id,
            id=invitation.person_id,
            archived_at__isnull=True,
        )
        .first()
    )
    if person is None or person.user_id is not None:
        raise _person_taken_error()
    person.user = user
    try:
        with transaction.atomic():
            person.save(update_fields=["user", "updated_at"])
    except IntegrityError as exc:
        # The account is still linked to another person here (from an earlier
        # membership): one person per account, so this one cannot be theirs.
        if _unique_constraint(exc) == _PERSON_USER_UNIQUE:
            raise _person_taken_error() from exc
        raise
    record(
        AuditCode.PERSON_LINKED,
        actor=AuditActor.for_user(user),
        target=AuditTarget(kind=TargetKind.PERSON, id=person.id, name=person.display_name),
        workspace_id=invitation.workspace_id,
    )
    return person

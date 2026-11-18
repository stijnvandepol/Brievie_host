"""Invitations (D 6.2): an admin makes a one-time link with which somebody joins.

The token is shown once, in the link's fragment; only its hash is stored. The role, the
person and the email address are fixed when the link is made; the invitee chooses none
of them.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User, normalise_email
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.links import fragment_link, home_network_only
from apps.common.privacy import mask_bsns
from apps.common.tokens import new_token
from apps.people.models import Person
from apps.workspaces.access import person_requires_email
from apps.workspaces.models import Invitation, Membership, Workspace

INVITATION_PATH = "/uitnodiging"
VALIDITY_DAYS = (1, 7, 30)
NOTE_MAX = 100


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

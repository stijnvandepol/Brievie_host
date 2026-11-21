"""Mensen: every person of a workspace with the state of their account (spec 3.1, 3.4).

The Person row is the account slot; a `User` and a `Membership` exist only from
activation (M1). The state is derived here and nowhere else:

- `active`: the person is linked to an account (so a membership exists);
- `invited`: an invitation names the person, neither accepted nor revoked (an expired one
  too, flagged);
- `deactivated` ("uitgeschakeld"): the account was switched off (`deactivated_at`);
- `inactive` ("niet actief"): none of these; also a person who never had an account.

Precedence: active > invited > deactivated > inactive. `former_user` is read here only,
for display (M4): the address and last sign-in of the account a switched-off person had.
Account facts (address, sign-in times, invitations, whether the caller may act on the
account) are for admins; for anybody else the state is only active or not (M5).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.devices.models import Device
from apps.people.models import DepartmentMember, Person
from apps.workspaces.models import Invitation, Membership, Workspace


class AccountState(StrEnum):
    INACTIVE = "inactive"
    INVITED = "invited"
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


# What somebody who is no admin may filter on: whether a person is active or not.
MEMBER_STATES = frozenset({AccountState.ACTIVE, AccountState.INACTIVE})


def account_state(
    *, user_id: UUID | None, deactivated_at: datetime | None, has_open_invitation: bool
) -> AccountState:
    """The state of one person's account; pure."""
    if user_id is not None:
        return AccountState.ACTIVE
    if has_open_invitation:
        return AccountState.INVITED
    if deactivated_at is not None:
        return AccountState.DEACTIVATED
    return AccountState.INACTIVE


@dataclass(frozen=True)
class PersonAccount:
    person: Person
    document_count: int
    state: AccountState
    role: str = ""
    sorts_post: bool = False
    department_ids: tuple[UUID, ...] = ()
    email: str = ""
    last_sign_in_at: datetime | None = None
    last_app_use_at: datetime | None = None
    deactivated_at: datetime | None = None
    invitation_id: UUID | None = None
    invitation_expires_at: datetime | None = None
    invitation_expired: bool = False
    can_manage_account: bool = False


def _invalid_state() -> ApiError:
    return ApiError(422, "validation_error", _("Kies een status uit de lijst."))


def _wanted_state(state: str, *, admin: bool) -> AccountState | None:
    """The filter, or None for every state; 422 for an unknown one (and, for somebody
    who is no admin, for a state only admins see)."""
    if not state:
        return None
    try:
        wanted = AccountState(state)
    except ValueError:
        raise _invalid_state() from None
    if not admin and wanted not in MEMBER_STATES:
        raise _invalid_state()
    return wanted


def _open_invitations(workspace: Workspace) -> dict[UUID, Invitation]:
    """Per person, the newest invitation naming them that is neither used nor revoked."""
    newest: dict[UUID, Invitation] = {}
    for invitation in Invitation.objects.filter(
        workspace=workspace, person__isnull=False, accepted_at=None, revoked_at=None
    ).order_by("created_at", "id"):
        if invitation.person_id is not None:
            newest[invitation.person_id] = invitation
    return newest


def list_people(
    workspace: Workspace, *, viewer: Membership, state: str = ""
) -> list[PersonAccount]:
    """Every person of kind person (departments are not people, M2), active ones by name
    and archived ones last, each with their account state. Needs the workspace's RLS
    context (the caller's, as `list_persons`: the letter counts are of letters the caller
    sees). A constant number of queries, whatever the number of people."""
    from apps.people.services import list_persons
    from apps.workspaces.services import list_members

    admin = viewer.role == Membership.Role.ADMIN
    wanted = _wanted_state(state, admin=admin)
    persons = [p for p in list_persons(workspace) if p.kind == Person.Kind.PERSON]
    memberships = {
        m.user_id: m
        for m in Membership.objects.filter(workspace=workspace).only(
            "user_id", "role", "sorts_post"
        )
    }
    departments: dict[UUID, list[UUID]] = {}
    for user_id, department_id in DepartmentMember.objects.filter(workspace=workspace).values_list(
        "user_id", "department_id"
    ):
        departments.setdefault(user_id, []).append(department_id)
    invitations = _open_invitations(workspace)

    accounts: dict[UUID, tuple[str, datetime | None]] = {}
    app_use: dict[UUID, datetime] = {}
    manageable: set[UUID] = set()
    if admin:
        account_ids = {
            user_id
            for p in persons
            for user_id in (p.user_id, p.former_user_id)
            if user_id is not None
        }
        accounts = {
            user_id: (email, last_login)
            for user_id, email, last_login in User.objects.filter(id__in=account_ids).values_list(
                "id", "email", "last_login"
            )
        }
        for user_id, seen in (
            Device.objects.filter(user_id__in=account_ids, revoked_at__isnull=True)
            .values("user_id")
            .annotate(seen=Max("last_seen_at"))
            .values_list("user_id", "seen")
        ):
            if seen is not None:
                app_use[user_id] = seen
        manageable = {
            m.user_id for m in list_members(workspace, viewer=viewer.user) if m.can_manage_account
        }

    now = timezone.now()
    result: list[PersonAccount] = []
    for person in persons:
        invitation = invitations.get(person.id)
        full = account_state(
            user_id=person.user_id,
            deactivated_at=person.deactivated_at,
            has_open_invitation=invitation is not None,
        )
        shown = (
            full
            if admin
            else (AccountState.ACTIVE if full == AccountState.ACTIVE else AccountState.INACTIVE)
        )
        if wanted is not None and shown != wanted:
            continue
        count = int(getattr(person, "document_count", 0))
        member_departments = (
            tuple(sorted(departments.get(person.user_id, []), key=str))
            if person.user_id is not None
            else ()
        )
        if not admin:
            result.append(
                PersonAccount(
                    person=person,
                    document_count=count,
                    state=shown,
                    department_ids=member_departments,
                )
            )
            continue
        membership = memberships.get(person.user_id) if person.user_id is not None else None
        account = person.user_id if person.user_id is not None else person.former_user_id
        email, last_login = accounts.get(account, ("", None)) if account is not None else ("", None)
        result.append(
            PersonAccount(
                person=person,
                document_count=count,
                state=shown,
                role=membership.role if membership is not None else "",
                sorts_post=membership.sorts_post if membership is not None else False,
                department_ids=member_departments,
                email=email,
                last_sign_in_at=last_login,
                last_app_use_at=app_use.get(account) if account is not None else None,
                deactivated_at=person.deactivated_at if person.user_id is None else None,
                invitation_id=invitation.id if invitation is not None else None,
                invitation_expires_at=invitation.expires_at if invitation is not None else None,
                invitation_expired=invitation is not None and invitation.expires_at <= now,
                can_manage_account=person.user_id in manageable,
            )
        )
    return result


def person_has_private_post(workspace: Workspace, person_id: UUID) -> bool:
    """Whether a private letter is on this person's name: a yes or no, never a count or a
    letter (asked by the "Uitschakelen" and "Uitnodigen" dialogs; the endpoint checks the
    admin). 404 `person_not_found` for a department or an unknown id. Needs the
    workspace's context; the letters are read as the system by `has_private_post`."""
    from apps.workspaces.access import person_requires_email

    person = Person.objects.filter(
        workspace=workspace, id=person_id, kind=Person.Kind.PERSON
    ).first()
    if person is None:
        raise ApiError(404, "person_not_found", _("We kunnen deze persoon niet vinden."))
    return person_requires_email(person)

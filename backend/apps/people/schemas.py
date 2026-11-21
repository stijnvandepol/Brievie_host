from datetime import datetime
from uuid import UUID

from ninja import Field, Schema

from apps.common.schemas import InputSchema
from apps.people.models import Person


class PersonOut(Schema):
    id: UUID
    display_name: str
    name_variants: list[str]
    user_id: UUID | None
    archived: bool
    # Documents this person is an addressee of.
    document_count: int
    # "person", or "department" (an "afdeling"; at home a "groep"): it has members
    # instead of one account. Show an unknown kind as a person.
    kind: str = "person"
    # A department's members and, of them, its leads ("leidinggevenden"); empty for a
    # person.
    member_user_ids: list[UUID] = Field(default_factory=list)
    lead_user_ids: list[UUID] = Field(default_factory=list)

    @staticmethod
    def resolve_archived(obj: Person) -> bool:
        return obj.archived_at is not None

    @staticmethod
    def resolve_member_user_ids(obj: Person) -> list[UUID]:
        return [m.user_id for m in obj.department_members.all()]

    @staticmethod
    def resolve_lead_user_ids(obj: Person) -> list[UUID]:
        return [m.user_id for m in obj.department_members.all() if m.is_lead]


class PersonAccountOut(Schema):
    """A person on Mensen with the state of their account (spec 3.1, 8). Departments are
    never listed. The account facts (`email`, the times, the invitation,
    `can_manage_account`, `role`, `sorts_post`) are filled for admins only; for anybody
    else they are empty and `state` is only `active` or `inactive`."""

    id: UUID
    display_name: str
    name_variants: list[str]
    archived: bool
    # Letters on this person's name that the caller sees.
    document_count: int
    # "inactive" (niet actief), "invited" (uitgenodigd), "active" (actief) or
    # "deactivated" (uitgeschakeld). Show an unknown state as "inactive".
    state: str
    # The account linked to the person (an active one).
    user_id: UUID | None = None
    role: str = ""
    sorts_post: bool = False
    # The departments the person's account is in.
    department_ids: list[UUID] = Field(default_factory=list)
    # The account's address; for a switched-off person the former account's.
    email: str = ""
    # The last sign-in on the web (any kind), and the last use of the app on a device that
    # is still paired; for a switched-off person the former account's.
    last_sign_in_at: datetime | None = None
    last_app_use_at: datetime | None = None
    # When the account was switched off ("uitgeschakeld").
    deactivated_at: datetime | None = None
    # The open invitation that names the person (the newest), for "Opnieuw versturen" and
    # "Intrekken"; expired ones too (`invitation_expired`).
    invitation_id: UUID | None = None
    # The role the open invitation gives (its renewal is previewed with it).
    invitation_role: str = ""
    invitation_expires_at: datetime | None = None
    invitation_expired: bool = False
    # Whether the caller may act on this account (reset link, sessions, devices).
    can_manage_account: bool = False
    # "Beheerd door": the member who reads this person's post while they have no active
    # account (admins only).
    managed_by_user_id: UUID | None = None


class PersonPrivatePostOut(Schema):
    # Whether a private letter is on the person's name; never a count or a letter.
    has_private_post: bool = False


class PersonIn(InputSchema):
    display_name: str = Field(min_length=1, max_length=150)
    # How the name is written on letters; trimmed, 0-20 of 2-100 characters each.
    # Left out on update: the stored variants stay.
    name_variants: list[str] = Field(default_factory=list)
    # Left out on update: the person stays archived or not, as stored.
    archived: bool = False
    # "person" or "department" (admins only); read when the person is made, never on
    # update, so an older client editing a department keeps it a department.
    kind: str = "person"


class PersonMemberIn(InputSchema):
    # The member to link; null unlinks.
    user_id: UUID | None = None
    # A link by which somebody gains sight needs these (S 4.4): see `previewVisibilityChange`.
    confirmed: bool = False
    preview_token: str = Field(default="", max_length=64)


class PersonManagerIn(InputSchema):
    # The member who manages the person's post ("Beheerd door"); null ends it.
    user_id: UUID | None = None
    # A gain needs these (S 4.4): see `previewVisibilityChange` with member_change "manage".
    confirmed: bool = False
    preview_token: str = Field(default="", max_length=64)


class EmergencyAccessUseIn(InputSchema):
    # Why the admin needs the post now; shown on every letter opened and to its people.
    reason: str = Field(min_length=10, max_length=200)


class EmergencyAccessUseOut(Schema):
    # Letters on the name that the admin could not see and now can, until `expires_at`.
    opened_count: int
    expires_at: datetime


class DepartmentMemberIn(InputSchema):
    user_id: UUID
    # The member leads the department: manages its labels (sees nothing extra).
    is_lead: bool = False
    # With departments on and "Iedereen ziet alle post" off the member gains sight: the
    # confirmed preview's token is needed (S 4.4).
    confirmed: bool = False
    preview_token: str = Field(default="", max_length=64)


class DepartmentMemberUpdateIn(InputSchema):
    is_lead: bool

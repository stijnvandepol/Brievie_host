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


class DepartmentMemberUpdateIn(InputSchema):
    is_lead: bool

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

    @staticmethod
    def resolve_archived(obj: Person) -> bool:
        return obj.archived_at is not None


class PersonIn(InputSchema):
    display_name: str = Field(min_length=1, max_length=150)
    # How the name is written on letters; trimmed, 0-20 of 2-100 characters each.
    # Left out on update: the stored variants stay.
    name_variants: list[str] = Field(default_factory=list)
    # Left out on update: the person stays archived or not, as stored.
    archived: bool = False


class PersonMemberIn(InputSchema):
    # The member to link; null unlinks.
    user_id: UUID | None = None

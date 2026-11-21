from uuid import UUID

from ninja import Field, Schema

from apps.common.schemas import InputSchema


class LabelOut(Schema):
    id: UUID
    name: str
    # One of grey, blue, teal, green, yellow, orange, red, purple; show an unknown one as grey.
    color: str
    # The preset label's stable key (`zorg`, `bank`, ...); empty for an own label.
    system_key: str
    # Documents that have this label.
    document_count: int
    # The department the label belongs to (offered to its members and admins only);
    # null for a label for everyone.
    department_id: UUID | None = None


class LabelIn(InputSchema):
    name: str = Field(min_length=1, max_length=50)
    # One of grey, blue, teal, green, yellow, orange, red, purple.
    color: str = "grey"
    # A label of this department (admins and its leads); null for everyone.
    department_id: UUID | None = None


class LabelUpdateIn(InputSchema):
    name: str = Field(min_length=1, max_length=50)
    # One of grey, blue, teal, green, yellow, orange, red, purple; empty keeps the colour.
    color: str = ""
    # Left out: the label keeps its department. Sent: the label moves (null: for
    # everyone); admins and leads of the departments concerned.
    department_id: UUID | None = None

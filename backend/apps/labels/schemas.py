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


class LabelIn(InputSchema):
    name: str = Field(min_length=1, max_length=50)
    # One of grey, blue, teal, green, yellow, orange, red, purple.
    color: str = "grey"

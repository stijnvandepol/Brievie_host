from uuid import UUID

from ninja import Schema
from pydantic import Field


class FolderOut(Schema):
    id: UUID
    name: str
    path: str
    parent_id: UUID | None


class FolderIn(Schema):
    name: str = Field(max_length=200)
    parent_id: UUID | None = None

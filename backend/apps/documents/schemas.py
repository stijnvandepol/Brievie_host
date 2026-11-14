from datetime import date, datetime
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


class PageMetaIn(Schema):
    """One page of an upload, in page order."""

    # Lower-case hex SHA-256 of the page's JPEG bytes.
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # The text recognised on the device; may be empty.
    text: str


class UploadMetadataIn(Schema):
    """The `metadata` field of `createDocument`, as a JSON string."""

    # Chosen by the client; repeating an upload with the same id is safe.
    client_id: UUID
    pages: list[PageMetaIn]


class DocumentSummaryOut(Schema):
    id: UUID
    client_id: UUID
    status: str
    page_count: int
    created_at: datetime
    sender: str
    document_type: str
    letter_date: date | None


class PageOut(Schema):
    index: int
    ocr_text: str


class ProposalOut(Schema):
    field: str
    group: int
    value: str
    # The words in the document this value was read from; empty for generated text.
    quote: str
    # Between 0 and 1, as a decimal string.
    confidence: str
    needs_attention: bool
    provider: str
    model: str


class TaskOut(Schema):
    id: UUID
    type: str
    description: str
    deadline: date | None
    deadline_source: str
    deadline_quote: str
    status: str


class DocumentOut(DocumentSummaryOut):
    # Why processing failed; empty otherwise.
    failure_code: str
    summary: str
    summary_by_ai: bool
    pages: list[PageOut]
    proposals: list[ProposalOut]
    tasks: list[TaskOut]

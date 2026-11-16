from datetime import date, datetime
from uuid import UUID

from ninja import Schema
from pydantic import Field

from apps.common.schemas import InputSchema
from apps.documents.models import DocumentEvent


class FolderOut(Schema):
    id: UUID
    name: str
    path: str
    parent_id: UUID | None


class FolderIn(InputSchema):
    name: str = Field(max_length=200)
    parent_id: UUID | None = None


class PageMetaIn(InputSchema):
    """One page of an upload, in page order."""

    # Lower-case hex SHA-256 of the page's JPEG bytes.
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    # The text recognised on the device; may be empty.
    text: str


class UploadMetadataIn(InputSchema):
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
    # The persons the document is "op naam van"; join with `listPersons`.
    addressee_ids: list[UUID]
    # Join with `listLabels`.
    label_ids: list[UUID]
    # "new", "review", "unassigned", or empty when the document is sorted.
    inbox_state: str
    # Proposals (labels, addressees) waiting for the user.
    open_suggestion_count: int


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


class SuggestionOut(Schema):
    id: UUID
    # "add_label" (then `label_id` is set) or "add_addressee" (then `person_id`).
    action: str
    label_id: UUID | None
    person_id: UUID | None
    # "rule", "name_match" or "ai".
    origin: str
    # The rule that proposed it; empty otherwise.
    rule_name: str
    # The words in the document that led to it; may be empty.
    quote: str
    # Between 0 and 1, as a decimal string.
    confidence: str
    # Shown ticked in the review (a strong name match).
    preselected: bool


class DocumentOut(DocumentSummaryOut):
    # Why processing failed; empty otherwise.
    failure_code: str
    summary: str
    summary_by_ai: bool
    pages: list[PageOut]
    proposals: list[ProposalOut]
    tasks: list[TaskOut]
    # Open proposals only, preselected first, then by confidence.
    suggestions: list[SuggestionOut]


class TaskIn(InputSchema):
    # One of the task types: pay, respond, object, apply, inspection, cancel, renew,
    # file_tax, other.
    type: str
    description: str = Field(min_length=1, max_length=500)
    deadline: date | None = None
    # "in_document" (then `deadline` and `deadline_quote` are required) or "manual".
    deadline_source: str
    deadline_quote: str = ""


class ConfirmIn(InputSchema):
    """What the user confirmed in the review. Nothing becomes a task without it."""

    sender: str = Field(max_length=200)
    # A document type code, or empty.
    document_type: str
    letter_date: date | None = None
    summary: str = Field(max_length=5000)
    # Whether `summary` is the AI's text (shown with a label) or the user's own.
    summary_by_ai: bool
    tasks: list[TaskIn] = Field(max_length=20)
    # When true, the document's labels and addressees become exactly `label_ids` and
    # `addressee_ids`, and the open suggestions not taken are dismissed. When false
    # (clients that do not know these fields), they are left alone.
    sets_organisation: bool = False
    label_ids: list[UUID] = Field(default=[], max_length=30)
    addressee_ids: list[UUID] = Field(default=[], max_length=10)


class AddresseesIn(InputSchema):
    # The document's addressees become exactly these persons.
    person_ids: list[UUID] = Field(max_length=10)


class LabelsIn(InputSchema):
    # The document's labels become exactly these labels.
    label_ids: list[UUID] = Field(max_length=30)


class InboxCountsOut(Schema):
    new: int
    review: int
    unassigned: int


class DocumentEventOut(Schema):
    id: UUID
    created_at: datetime
    kind: str
    origin: str
    # Names are copies, so they stay readable after a delete; empty when not applicable.
    actor_name: str
    rule_name: str
    label_name: str
    person_name: str
    can_undo: bool
    undone: bool

    @staticmethod
    def resolve_actor_name(obj: DocumentEvent) -> str:
        return obj.actor.name if obj.actor is not None else ""

    @staticmethod
    def resolve_can_undo(obj: DocumentEvent) -> bool:
        # A change a rule made, not undone yet, whose label or person is still there
        # (`undone` and `still_linked` are annotations of `list_document_events`).
        return (
            obj.origin == DocumentEvent.Origin.RULE
            and obj.kind in (DocumentEvent.Kind.LABEL_ADDED, DocumentEvent.Kind.ADDRESSEE_ADDED)
            and not getattr(obj, "undone", False)
            and bool(getattr(obj, "still_linked", False))
        )

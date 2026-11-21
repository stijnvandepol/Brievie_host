from datetime import date, datetime
from uuid import UUID

from ninja import Schema
from pydantic import AwareDatetime, Field

from apps.common.schemas import InputSchema
from apps.documents.models import DocumentEvent
from apps.documents.organise import AUTOMATIC_ORIGINS, UNDOABLE_ORIGINS


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
    addressee_ids: list[UUID] = Field(default_factory=list)
    # Join with `listLabels`.
    label_ids: list[UUID] = Field(default_factory=list)
    # "new", "review", "unassigned", or empty when the document is sorted.
    inbox_state: str = ""
    # Proposals (labels, addressees) waiting for the user.
    open_suggestion_count: int = 0
    # The AI confirmed this letter itself (the autopilot); `reopenDocument` takes it back.
    confirmed_by_ai: bool = False
    # Of `label_ids` and `addressee_ids`: the ones Brievie put there itself (the autopilot).
    auto_label_ids: list[UUID] = Field(default_factory=list)
    auto_addressee_ids: list[UUID] = Field(default_factory=list)
    # Of `auto_label_ids`: the ones the autopilot put there because a rule proposed them
    # (not the AI's own choice): show them as done by a rule.
    auto_rule_label_ids: list[UUID] = Field(default_factory=list)
    # Before a person confirmed the letter: the pipeline's strongest proposal for an empty
    # `sender`, `document_type` and `letter_date`, as the detail's `proposals` show it. A
    # proposal, never a fact: show it as one. Empty (null) once confirmed or without one.
    proposed_sender: str = ""
    proposed_document_type: str = ""
    proposed_letter_date: date | None = None
    # Open duplicate pairs with this letter that the caller sees in full (`listDuplicates`):
    # show a chip "Dubbel?". 0 for a viewer.
    open_duplicate_count: int = 0
    # "workspace", or "restricted": private, only its readers see it (show a lock).
    visibility: str = "workspace"
    # Of a private letter: who reads it ("Privé voor Anne en Bob"), by name. Filled in the
    # list and on the letter; empty elsewhere and for a letter that is not private.
    reader_names: list[str] = Field(default_factory=list)


class TrashedDocumentOut(DocumentSummaryOut):
    """A letter in the trash: its summary row, when and by whom it was trashed, and when
    it is purged (clients never compute the retention themselves)."""

    trashed_at: datetime
    # Empty when Brievie did it.
    trashed_by_name: str = ""
    # `trashed_at` plus the retention (30 days); purged within the hour after.
    purge_after: datetime


class TrashListOut(Schema):
    """The caller's trash, the most recently trashed first (at most 500)."""

    items: list[TrashedDocumentOut] = Field(default_factory=list)
    # How many days a letter stays in the trash.
    retention_days: int = 30
    # How many letters the caller's trash holds, also beyond the 500 listed: what
    # `emptyTrash` without a newer `up_to` would purge.
    total: int = 0


class EmptyTrashIn(InputSchema):
    """Empty the trash up to the newest `trashed_at` the caller showed (all when absent):
    a letter trashed while the admin read the question is not emptied. With an offset
    only (422 otherwise): a moment without one would be read in the server's zone."""

    up_to: AwareDatetime | None = None


class EmptyTrashOut(Schema):
    # How many letters are now being purged.
    document_count: int


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
    completed_at: datetime | None = None
    # Made by the AI when it confirmed the letter itself.
    created_by_ai: bool = False
    # What a payment costs, a decimal string ("1234.56"); empty when unknown.
    amount: str = ""
    # For a task without a date: the letter's words that name a term ("Datum nodig").
    term_quote: str = ""
    # The member it is given to ("Aan Anne gegeven"); empty and null when nobody.
    assigned_to_name: str = ""
    assigned_to_id: UUID | None = None


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
    suggestions: list[SuggestionOut] = Field(default_factory=list)
    # False after a change that handed the letter to someone else: the caller no longer
    # sees it, and this answer holds its id and status only (S 4.13).
    can_read: bool = True
    # The members the letter is shared with ("Delen met"), any letter.
    shared_user_ids: list[UUID] = Field(default_factory=list)
    # Whether the caller may make this private letter visible to everyone again.
    can_make_public: bool = False


class TaskIn(InputSchema):
    # One of the task types: pay, respond, object, apply, inspection, cancel, renew,
    # file_tax, other.
    type: str
    description: str = Field(min_length=1, max_length=500)
    deadline: date | None = None
    # "in_document" (then `deadline` and `deadline_quote` are required) or "manual".
    deadline_source: str
    deadline_quote: str = ""
    # What a payment costs, as "1234.56" or "1234,56"; empty when unknown (older clients
    # send nothing: no amount is stored).
    amount: str = Field(default="", max_length=20)


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
    # "" leaves the letter as it is (a Zorg label among the labels still makes it
    # private), "restricted" makes it private, "workspace" visible to everyone again.
    visibility: str = ""


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
    # Documents the autopilot confirmed or put a label or addressee on.
    automatic: int = 0
    # Open duplicate pairs the caller sees in full (`listDuplicates`); 0 for a viewer.
    duplicate: int = 0


class DocumentEventOut(Schema):
    id: UUID
    created_at: datetime
    # "label_added", "label_removed", "addressee_added", "addressee_removed", "reopened", ...
    kind: str
    # "user", "rule", "suggestion", "undo", or "ai", "name_match" and "rule_autopilot"
    # (the autopilot; "rule_autopilot" applied a rule's label, `rule_name` names it).
    origin: str
    # The user who made the change; null for a rule, the system, or a deleted user.
    actor_id: UUID | None = None
    # Names are copies, so they stay readable after a delete; empty when not applicable.
    actor_name: str
    rule_name: str
    label_name: str
    person_name: str
    can_undo: bool
    undone: bool
    # The other letter of a duplicate pair (`merged`, `duplicate_dismissed`, `task_moved`,
    # and `trashed` with origin `merge`); null otherwise or once that letter is purged.
    related_document_id: UUID | None = None
    # A short text with the line: the reason of an emergency opening, or the name of whom
    # a grant gave sight (`access_granted`). Empty otherwise.
    note: str = ""

    @staticmethod
    def resolve_actor_name(obj: DocumentEvent) -> str:
        return obj.actor.name if obj.actor is not None else ""

    @staticmethod
    def resolve_can_undo(obj: DocumentEvent) -> bool:
        # A change a rule or the autopilot made, not undone yet, whose label or person is
        # still there and had nothing happen to it since (annotations of `list_document_events`).
        # The autopilot's change only while its link is still automatic (no person confirmed it).
        return (
            obj.origin in UNDOABLE_ORIGINS
            and obj.kind in (DocumentEvent.Kind.LABEL_ADDED, DocumentEvent.Kind.ADDRESSEE_ADDED)
            and not getattr(obj, "undone", False)
            and bool(getattr(obj, "still_linked", False))
            and not getattr(obj, "superseded", True)
            and (
                obj.origin not in AUTOMATIC_ORIGINS or bool(getattr(obj, "still_automatic", False))
            )
        )


class DuplicateDifferenceOut(Schema):
    # "sender", "letter_date", "document_type", "amount", "invoice_number",
    # "payment_reference" or "page_count".
    field: str
    # The value on `first` and on `second`: dates ISO, amounts as decimal text.
    first: str
    second: str
    # One side was read with little confidence: maybe a misread, maybe a real difference.
    uncertain: bool = False


class TaskTwinOut(Schema):
    """A done task on one letter with an equal open task on the other: a merge that keeps
    `open_document_id` can complete it (`complete_twin_tasks`)."""

    done_document_id: UUID
    open_document_id: UUID
    type: str
    description: str


class DuplicateOut(Schema):
    """Two letters that are probably one letter scanned twice. `first` and `second` are in
    id order; the caller sees both."""

    id: UUID
    # "open" (the lists only answer open pairs).
    state: str
    created_at: datetime
    first: DocumentSummaryOut
    second: DocumentSummaryOut
    # The letter to keep by default: the confirmed one, else the one further in review,
    # else the older.
    suggested_keep_id: UUID
    # How alike the two texts are, 0 to 100.
    text_similarity: int
    # The facts both letters share (field names as in `differences`).
    same: list[str] = Field(default_factory=list)
    # The facts that differ, uncertain ones first.
    differences: list[DuplicateDifferenceOut] = Field(default_factory=list)
    task_twins: list[TaskTwinOut] = Field(default_factory=list)
    # False when the two letters are not seen by the same people: a merge answers 409
    # `duplicate_visibility_differs`; offer "Het zijn twee brieven".
    can_merge: bool = True
    # "visibility_differs" when `can_merge` is false; empty otherwise.
    blocked_reason: str = ""


class MergeDuplicateIn(InputSchema):
    # One of the pair's two letters: it stays, the other goes to the trash.
    keep_document_id: UUID
    # Complete an open task on the kept letter whose equal is done on the other one
    # (`task_twins`).
    complete_twin_tasks: bool = True


class MyExportOut(Schema):
    """The member's own export of their post."""

    # Whether the workspace lets members export (an admin can switch it off).
    allowed: bool
    # `none`, `pending` (being built), `ready` (until `expires_at`) or `failed`.
    status: str
    id: UUID | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    document_count: int = 0
    # Bytes of the sealed file.
    size: int = 0


class DocumentSearchIn(InputSchema):
    """A search in the letters of the workspace. POST, so the term is never in a URL."""

    # Words to find; a BSN in it is removed (`bsn_removed`), and so is the literal [BSN].
    q: str = Field(max_length=200)
    # A document type code; empty for all.
    document_type: str = ""
    label_id: UUID | None = None
    person_id: UUID | None = None
    # "new", "review" or "unassigned"; empty for all.
    inbox_state: str = ""
    # On the letter date; a letter without one falls under its upload date.
    date_from: date | None = None
    date_to: date | None = None
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1000)


class SnippetPartOut(Schema):
    """A piece of the fragment; `hit` pieces are what matched. Plain text, never HTML."""

    text: str
    hit: bool


class DocumentSearchHitOut(DocumentSummaryOut):
    # A short fragment (at most 240 characters) with the hit marked.
    snippet: list[SnippetPartOut] = Field(default_factory=list)


class DocumentSearchOut(Schema):
    items: list[DocumentSearchHitOut]
    # The `offset` of the next page; null when there is none.
    next_offset: int | None = None
    # No exact hit: these letters only look like the term (a typo, another spelling).
    fuzzy: bool = False
    # A BSN was taken out of the term before searching.
    bsn_removed: bool = False


class DocumentVisibilityIn(InputSchema):
    # "restricted" (private) or "workspace".
    visibility: str
    # The members the letter is shared with, afterwards exactly these (any letter).
    shared_user_ids: list[UUID] = Field(default=[], max_length=50)


class DocumentVisibilityOut(Schema):
    visibility: str
    reader_names: list[str] = Field(default_factory=list)
    shared_user_ids: list[UUID] = Field(default_factory=list)
    # False when the caller no longer reads the letter (they removed their own share).
    can_read: bool = True


class DocumentViewerOut(Schema):
    user_id: UUID
    name: str
    # Why this member sees the letter, in words: "op naam van Jan", "afdeling Finance",
    # "verdeelt post", "heeft de brief gescand", "is gedeeld", "ziet alle post",
    # "beheerder, leest alle post", "noodtoegang tot 14:30".
    reasons: list[str]


class DocumentAccessOut(Schema):
    """Who can see this letter, by name ("Wie kan deze brief zien?")."""

    viewers: list[DocumentViewerOut]

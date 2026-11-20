from datetime import date
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.accounts.models import User
from apps.common import crypto
from apps.common.errors import ApiError
from apps.documents import organise, suggestions, visibility
from apps.documents.intake import page_aad
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentPage,
    DocumentSuggestion,
    Extraction,
    Folder,
)
from apps.documents.schemas import (
    DocumentOut,
    DocumentSummaryOut,
    PageOut,
    ProposalOut,
    SuggestionOut,
    TaskOut,
)
from apps.documents.storage import default_storage
from apps.pipeline.jobs import defer_processing
from apps.tasks.models import Task
from apps.workspaces.context import current_actor
from apps.workspaces.keys import workspace_key
from apps.workspaces.locks import lock_own_membership
from apps.workspaces.models import Membership, Workspace

_PATH_UNIQUE = "documents_folder_workspace_path_unique"


def list_folders(workspace: Workspace) -> list[Folder]:
    """Folders of the workspace whose RLS context the caller has entered."""
    return list(Folder.objects.filter(workspace=workspace).order_by("path"))


def create_folder(*, workspace: Workspace, name: str, parent_id: UUID | None) -> Folder:
    """Create a folder; needs the workspace's RLS context (see workspaces.deps).

    A parent is looked up under RLS, so one from another workspace is not found.
    """
    with transaction.atomic():
        parent = None
        if parent_id is not None:
            parent = Folder.objects.filter(workspace=workspace, id=parent_id).first()
            if parent is None:
                raise ApiError(404, "folder_not_found", _("We kunnen deze map niet vinden."))
        folder = Folder(workspace=workspace, parent=parent, name=name)
        try:
            with transaction.atomic():
                folder.save()
        except ValidationError as exc:
            raise ApiError(422, "validation_error", exc.messages[0]) from exc
        except IntegrityError as exc:
            cause = exc.__cause__
            if (
                isinstance(cause, pg_errors.UniqueViolation)
                and cause.diag.constraint_name == _PATH_UNIQUE
            ):
                raise ApiError(
                    409, "folder_exists", _("Er is al een map met deze naam op deze plek.")
                ) from exc
            raise
    return folder


class InboxState:
    NEW = "new"
    REVIEW = "review"
    UNASSIGNED = "unassigned"
    SORTED = ""


INBOX_STATES = (InboxState.NEW, InboxState.REVIEW, InboxState.UNASSIGNED)
_REVIEW_STATUSES = (Document.Status.NEEDS_REVIEW, Document.Status.FAILED)


def inbox_state(document: Document) -> str:
    """Where the document stands in the inbox; derived, not stored (design 11)."""
    if document.status == Document.Status.PROCESSING:
        return InboxState.NEW
    if document.status in _REVIEW_STATUSES:
        return InboxState.REVIEW
    if not _addressee_ids(document):
        return InboxState.UNASSIGNED
    return InboxState.SORTED


def _addressee_ids(document: Document) -> list[UUID]:
    # `.all()` so a prefetch (list_documents) is used.
    return [link.person_id for link in document.addressee_links.all()]


def _label_ids(document: Document) -> list[UUID]:
    # `.all()` so a prefetch (list_documents) is used.
    return [link.label_id for link in document.label_links.all()]


def _auto_addressee_ids(document: Document) -> list[UUID]:
    return [link.person_id for link in document.addressee_links.all() if link.automatic]


def _auto_label_ids(document: Document) -> list[UUID]:
    return [link.label_id for link in document.label_links.all() if link.automatic]


def _auto_rule_label_ids(document: Document) -> list[UUID]:
    """The automatic labels whose accepted proposal came from a rule (the autopilot applied
    a rule's label). Read from the suggestions, so a list's prefetch is used."""
    by_rule = {
        s.label_id
        for s in document.suggestions.all()
        if s.label_id is not None
        and s.state == DocumentSuggestion.State.ACCEPTED
        and s.origin == DocumentSuggestion.Origin.RULE
    }
    return [label_id for label_id in _auto_label_ids(document) if label_id in by_rule]


# The prefetch of a document's suggestions wherever several documents are shown: with
# their persons, so `_open_suggestions` can leave out archived ones without a query each.
SUGGESTIONS = models.Prefetch(
    "suggestions", queryset=DocumentSuggestion.objects.select_related("person")
)


_HEADLINE_FIELDS = (
    Extraction.Field.SENDER,
    Extraction.Field.DOCUMENT_TYPE,
    Extraction.Field.LETTER_DATE,
)
# The proposals a list row shows for a letter not confirmed yet; one query for the list.
HEADLINE_PROPOSALS = models.Prefetch(
    "extractions",
    queryset=Extraction.objects.filter(group=0, field__in=_HEADLINE_FIELDS).order_by(
        "-confidence", "created_at", "id"
    ),
    to_attr="headline_proposals",
)


def _headline_proposals(document: Document) -> dict[str, str]:
    """Per field, the strongest proposal for what a person has not confirmed yet.

    A value found nowhere in the letter (confidence 0) is no proposal to show; a type or
    date that does not parse is left out. Uses `HEADLINE_PROPOSALS` when prefetched.
    """
    if document.confirmed_at is not None:
        return {}
    rows: list[Extraction] | None = getattr(document, "headline_proposals", None)
    if rows is None:
        models.prefetch_related_objects([document], HEADLINE_PROPOSALS)
        rows = getattr(document, "headline_proposals", [])
    best: dict[str, str] = {}
    for row in rows:
        if row.field in best or not row.value:
            continue
        if row.field != Extraction.Field.DOCUMENT_TYPE and not row.confidence > 0:
            continue
        if row.field == Extraction.Field.DOCUMENT_TYPE and row.value not in Document.Type.values:
            continue
        if row.field == Extraction.Field.LETTER_DATE and _iso_date(row.value) is None:
            continue
        best[row.field] = row.value
    return best


def _iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _open_suggestions(document: Document) -> list[DocumentSuggestion]:
    """Preselected first, then by confidence. `.all()` so a prefetch is used.

    A proposal of a person archived since is left out: it can no longer be accepted
    (archiving removes it as well; this covers a row left from before).
    """
    rows = [
        s
        for s in document.suggestions.all()
        if s.state == DocumentSuggestion.State.OPEN
        and (s.person is None or s.person.archived_at is None)
    ]
    return sorted(rows, key=lambda s: (not s.preselected, -s.confidence, s.created_at, s.id))


def _is_automatic() -> models.Q:
    """The AI confirmed the document or put a label or addressee on it itself.

    The links as two uncorrelated subqueries, each hashed once, instead of a probe per
    letter (measured, DECISIONS 2026-11-20).
    """
    labels = DocumentLabel.objects.filter(automatic=True).values("document_id")
    addressees = DocumentAddressee.objects.filter(automatic=True).values("document_id")
    return models.Q(confirmed_by_ai=True) | models.Q(id__in=labels) | models.Q(id__in=addressees)


def _in_state(state: str) -> models.Q:
    if state == InboxState.NEW:
        return models.Q(status=Document.Status.PROCESSING)
    if state == InboxState.REVIEW:
        return models.Q(status__in=_REVIEW_STATUSES)
    # `addressee_ids` mirrors the links (one writer, checked nightly); this form uses the
    # partial index `documents_document_unassigned`.
    return models.Q(status=Document.Status.DONE, addressee_ids=[])


def list_documents(
    membership: Membership,
    *,
    inbox_state: str = "",
    person_id: UUID | None = None,
    label_id: UUID | None = None,
    automatic: bool = False,
) -> list[Document]:
    """The newest documents the member sees (at most 200; paging comes later), filtered.

    `automatic` keeps the documents the autopilot confirmed or organised.
    """
    if inbox_state and inbox_state not in INBOX_STATES:
        raise ApiError(422, "validation_error", _("Deze stand van de inbox kennen we niet."))
    documents = visibility.visible_documents(membership)
    if inbox_state:
        documents = documents.filter(_in_state(inbox_state))
    if person_id is not None:
        # The GIN index on `addressee_ids`.
        documents = documents.filter(addressee_ids__contains=[person_id])
    if label_id is not None:
        documents = documents.filter(label_links__label_id=label_id)
    if automatic:
        documents = documents.filter(_is_automatic())
    return list(
        documents.prefetch_related(
            "addressee_links", "label_links", SUGGESTIONS, HEADLINE_PROPOSALS
        ).order_by("-created_at", "-id")[:200]
    )


def inbox_counts(membership: Membership) -> dict[str, int]:
    """How many of the documents the member sees are in each inbox state, for the
    badges: one pass."""
    counts = visibility.visible_documents(membership).aggregate(
        **{state: models.Count("id", filter=_in_state(state)) for state in INBOX_STATES},
        automatic=models.Count("id", filter=_is_automatic()),
    )
    return {name: int(counts[name]) for name in (*INBOX_STATES, "automatic")}


def get_document(*, membership: Membership, document_id: UUID) -> Document:
    """A document the member sees, or 404 (one they may not see is not found either)."""
    document = visibility.visible_documents(membership).filter(id=document_id).first()
    if document is None:
        raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
    return document


def document_summary_out(document: Document) -> DocumentSummaryOut:
    """A row of a document list."""
    return _summary_out(document, _open_suggestions(document))


def _summary_out(
    document: Document, open_suggestions: list[DocumentSuggestion]
) -> DocumentSummaryOut:
    proposed = _headline_proposals(document)
    return DocumentSummaryOut(
        id=document.id,
        client_id=document.client_id,
        status=document.status,
        page_count=document.page_count,
        created_at=document.created_at,
        sender=document.sender,
        document_type=document.document_type,
        letter_date=document.letter_date,
        addressee_ids=_addressee_ids(document),
        label_ids=_label_ids(document),
        inbox_state=inbox_state(document),
        open_suggestion_count=len(open_suggestions),
        confirmed_by_ai=document.confirmed_by_ai,
        auto_label_ids=_auto_label_ids(document),
        auto_rule_label_ids=_auto_rule_label_ids(document),
        auto_addressee_ids=_auto_addressee_ids(document),
        proposed_sender="" if document.sender else proposed.get(Extraction.Field.SENDER, ""),
        proposed_document_type=""
        if document.document_type
        else proposed.get(Extraction.Field.DOCUMENT_TYPE, ""),
        proposed_letter_date=None
        if document.letter_date
        else _iso_date(proposed.get(Extraction.Field.LETTER_DATE, "")),
    )


def document_out_for(document: Document) -> DocumentOut:
    """The answer of an organising endpoint: the full document while the caller still
    sees it, else only its id and status with `can_read` false (S 4.9.3: `can_read` is
    computed last, as the caller; nothing of the letter is read as the caller after)."""
    if visibility.can_read(document.id):
        return document_out(document)
    return DocumentOut(
        id=document.id,
        client_id=document.client_id,
        status=visibility.status_of(document.id) or document.status,
        page_count=0,
        created_at=document.created_at,
        sender="",
        document_type="",
        letter_date=None,
        failure_code="",
        summary="",
        summary_by_ai=False,
        pages=[],
        proposals=[],
        tasks=[],
        suggestions=[],
        addressee_ids=[],
        label_ids=[],
        inbox_state="",
        can_read=False,
    )


def document_out(document: Document) -> DocumentOut:
    """The document with its pages, proposals and tasks, as the API shows it."""
    models.prefetch_related_objects([document], SUGGESTIONS)
    open_suggestions = _open_suggestions(document)
    return DocumentOut(
        **_summary_out(document, open_suggestions).model_dump(),
        failure_code=document.failure_code,
        summary=document.summary,
        summary_by_ai=document.summary_by_ai,
        pages=[
            PageOut(index=page.index, ocr_text=page.ocr_text)
            for page in document.pages.order_by("index")
        ],
        proposals=[
            ProposalOut(
                field=e.field,
                group=e.group,
                value=e.value,
                quote=e.quote,
                confidence=f"{e.confidence:.3f}",
                needs_attention=e.needs_attention,
                provider=e.provider,
                model=e.model,
            )
            for e in document.extractions.order_by("group", "created_at", "id")
        ],
        tasks=[
            TaskOut(
                id=task.id,
                type=task.type,
                description=task.description,
                deadline=task.deadline,
                deadline_source=task.deadline_source,
                deadline_quote=task.deadline_quote,
                status=task.status,
                completed_at=task.completed_at,
                created_by_ai=task.created_by_ai,
            )
            for task in document.tasks.order_by("deadline", "created_at", "id")
        ],
        suggestions=[
            SuggestionOut(
                id=s.id,
                action=s.action,
                label_id=s.label_id,
                person_id=s.person_id,
                origin=s.origin,
                rule_name=s.rule_name,
                quote=s.quote,
                confidence=f"{s.confidence:.3f}",
                preselected=s.preselected,
            )
            for s in open_suggestions
        ],
    )


def read_page(*, membership: Membership, document_id: UUID, index: int) -> tuple[bytes, str]:
    """The decrypted page file and its content type."""
    workspace = membership.workspace
    document = get_document(membership=membership, document_id=document_id)
    page = DocumentPage.objects.filter(document=document, index=index).first()
    if page is None:
        raise ApiError(404, "page_not_found", _("We kunnen deze pagina niet vinden."))
    sealed = default_storage().read(page.storage_path)
    data = crypto.open_sealed(
        workspace_key(workspace.id), sealed, aad=page_aad(workspace.id, document.id, page.index)
    )
    return data, page.content_type


def retry_processing(*, workspace: Workspace, document_id: UUID) -> Document:
    """Queue a failed document for processing again."""
    with transaction.atomic():
        _lock_own_membership(workspace)
        document = (
            Document.objects.select_for_update(no_key=True)
            .filter(workspace=workspace, id=document_id)
            .first()
        )
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
        if document.status != Document.Status.FAILED:
            raise ApiError(
                409,
                "document_not_failed",
                _("Dit document wordt nog verwerkt. Probeer het zo opnieuw.")
                if document.status == Document.Status.PROCESSING
                else _("Dit document hoeft niet opnieuw verwerkt te worden."),
            )
        document.status = Document.Status.PROCESSING
        document.failure_code = ""
        document.save(update_fields=["status", "failure_code", "updated_at"])
        defer_processing(workspace_id=workspace.id, document_id=document.id)
    return document


def reopen_document(*, workspace: Workspace, user: User, document_id: UUID) -> Document:
    """Take the AI's confirmation back: the document returns to review.

    Only a `done` document the AI confirmed. The confirmed fields are emptied and the
    tasks the AI made that are still open are removed (a task somebody ticked off stays).
    Labels, addressees, proposals and suggestions stay, so the normal review follows.
    """
    with transaction.atomic():
        lock_own_membership(workspace.id, user.id)
        document = (
            Document.objects.select_for_update(no_key=True)
            .filter(workspace=workspace, id=document_id)
            .first()
        )
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
        if document.status != Document.Status.DONE or not document.confirmed_by_ai:
            raise ApiError(
                409,
                "document_not_reopenable",
                _("Dit document kun je niet opnieuw controleren."),
            )
        document.status = Document.Status.NEEDS_REVIEW
        document.sender = ""
        document.document_type = ""
        document.summary = ""
        document.letter_date = None
        document.confirmed_at = None
        document.confirmed_organisation = None
        document.summary_by_ai = False
        document.confirmed_by_ai = False
        document.save()
        Task.objects.filter(document=document, created_by_ai=True, status=Task.Status.OPEN).delete()
        organise.record_reopened(document, actor=user)
    return document


def set_document_addressees(
    *, workspace: Workspace, user: User, document_id: UUID, person_ids: list[UUID]
) -> Document:
    """The document's addressees become exactly `person_ids`."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        with visibility.addressee_scope(document):
            organise.set_addressees(document=document, person_ids=person_ids, actor=user)
    return document


def set_document_labels(
    *, workspace: Workspace, user: User, document_id: UUID, label_ids: list[UUID]
) -> Document:
    """The document's labels become exactly `label_ids`."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        organise.set_labels(document=document, label_ids=label_ids, actor=user)
    return document


def accept_suggestion(
    *, workspace: Workspace, user: User, document_id: UUID, suggestion_id: UUID
) -> Document:
    """Make the proposed change, as the user's choice; accepting again changes nothing."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        suggestion = _suggestion(document, suggestion_id)
        if suggestion.state == DocumentSuggestion.State.ACCEPTED:
            return document
        origin = DocumentEvent.Origin.SUGGESTION
        if suggestion.label is not None:
            organise.add_label(document, suggestion.label, actor=user, origin=origin)
        elif suggestion.person is not None:
            if suggestion.person.archived_at is not None:
                raise ApiError(
                    422,
                    "validation_error",
                    _("Een gearchiveerde persoon kun je niet aan een document toevoegen."),
                )
            with visibility.addressee_scope(document):
                organise.add_addressee(document, suggestion.person, actor=user, origin=origin)
        suggestion.state = DocumentSuggestion.State.ACCEPTED
        suggestion.save(update_fields=["state", "updated_at"])
    return document


def dismiss_suggestion(*, workspace: Workspace, document_id: UUID, suggestion_id: UUID) -> Document:
    """Say no to an open proposal; the document itself does not change."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        suggestion = _suggestion(document, suggestion_id)
        if suggestion.state == DocumentSuggestion.State.OPEN:
            suggestions.mark_dismissed(
                document, suggestion.action, suggestions.target_id_of(suggestion)
            )
    return document


def _suggestion(document: Document, suggestion_id: UUID) -> DocumentSuggestion:
    suggestion = (
        DocumentSuggestion.objects.select_for_update(of=("self",))
        .select_related("label", "person")
        .filter(document=document, id=suggestion_id)
        .first()
    )
    if suggestion is None:
        raise ApiError(404, "suggestion_not_found", _("We kunnen dit voorstel niet vinden."))
    return suggestion


def _lock_own_membership(workspace: Workspace) -> None:
    """The request's membership first (`apps.workspaces.locks`); the system has none."""
    actor = current_actor()
    if isinstance(actor, UUID):
        lock_own_membership(workspace.id, actor)


def _lock_for_organising(workspace: Workspace, document_id: UUID) -> Document:
    """The document, locked after the caller's membership (the lock order); refused while
    it is still being processed."""
    _lock_own_membership(workspace)
    document = (
        Document.objects.select_for_update(no_key=True)
        .filter(workspace=workspace, id=document_id)
        .first()
    )
    if document is None:
        raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
    if document.status == Document.Status.PROCESSING:
        raise ApiError(
            409,
            "document_processing",
            _("Dit document wordt nog verwerkt. Probeer het zo opnieuw."),
        )
    return document


def list_document_events(*, membership: Membership, document_id: UUID) -> list[DocumentEvent]:
    """The document's history, newest first.

    Annotated with `undone` (an undo event points at it), `still_linked` (its label or
    person is on the document now), `still_automatic` (that link is still marked
    automatic) and `superseded` (a later event concerns the same label or person), which
    `DocumentEventOut.can_undo` reads.
    """
    document = get_document(membership=membership, document_id=document_id)
    label_linked = DocumentLabel.objects.filter(
        document_id=models.OuterRef("document_id"), label_id=models.OuterRef("label_id")
    )
    person_linked = DocumentAddressee.objects.filter(
        document_id=models.OuterRef("document_id"), person_id=models.OuterRef("person_id")
    )
    later = DocumentEvent.objects.filter(
        document_id=models.OuterRef("document_id"), created_at__gt=models.OuterRef("created_at")
    )
    return list(
        DocumentEvent.objects.filter(document=document)
        .select_related("actor")
        .annotate(
            undone=models.Exists(DocumentEvent.objects.filter(undoes=models.OuterRef("pk"))),
            still_linked=models.Exists(label_linked) | models.Exists(person_linked),
            still_automatic=models.Exists(label_linked.filter(automatic=True))
            | models.Exists(person_linked.filter(automatic=True)),
            superseded=models.Exists(later.filter(label_id=models.OuterRef("label_id")))
            | models.Exists(later.filter(person_id=models.OuterRef("person_id"))),
        )
        .order_by("-created_at", "-id")
    )


def undo_document_event(
    *, workspace: Workspace, user: User, document_id: UUID, event_id: UUID
) -> Document:
    """Reverse a change a rule made; the rule will not make it again on this document."""
    with transaction.atomic():
        document = _lock_for_organising(workspace, document_id)
        with visibility.addressee_scope(document):
            organise.undo_event(document=document, event_id=event_id, actor=user)
    return document

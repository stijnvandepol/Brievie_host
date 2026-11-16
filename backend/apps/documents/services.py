from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.accounts.models import User
from apps.common import crypto
from apps.common.errors import ApiError
from apps.documents import organise
from apps.documents.intake import page_aad
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentPage,
    Folder,
)
from apps.documents.schemas import (
    DocumentOut,
    DocumentSummaryOut,
    PageOut,
    ProposalOut,
    TaskOut,
)
from apps.documents.storage import default_storage
from apps.pipeline.jobs import defer_processing
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Workspace

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


def _has_addressee() -> models.Exists:
    return models.Exists(DocumentAddressee.objects.filter(document=models.OuterRef("pk")))


def _in_state(state: str) -> models.Q:
    if state == InboxState.NEW:
        return models.Q(status=Document.Status.PROCESSING)
    if state == InboxState.REVIEW:
        return models.Q(status__in=_REVIEW_STATUSES)
    return models.Q(status=Document.Status.DONE, has_addressee=False)


def list_documents(
    workspace: Workspace,
    *,
    inbox_state: str = "",
    person_id: UUID | None = None,
    label_id: UUID | None = None,
) -> list[Document]:
    """The newest documents of the workspace (at most 200; paging comes later), filtered."""
    if inbox_state and inbox_state not in INBOX_STATES:
        raise ApiError(422, "validation_error", _("Deze stand van de inbox kennen we niet."))
    if label_id is not None:
        # Labels arrive in a later step; until then no document has one.
        return []
    documents = Document.objects.filter(workspace=workspace).annotate(
        has_addressee=_has_addressee()
    )
    if inbox_state:
        documents = documents.filter(_in_state(inbox_state))
    if person_id is not None:
        documents = documents.filter(addressee_links__person_id=person_id)
    return list(documents.prefetch_related("addressee_links").order_by("-created_at", "-id")[:200])


def inbox_counts(workspace: Workspace) -> dict[str, int]:
    """How many documents are in each inbox state, for the badges."""
    documents = Document.objects.filter(workspace=workspace).annotate(
        has_addressee=_has_addressee()
    )
    counts = documents.aggregate(
        **{state: models.Count("id", filter=_in_state(state)) for state in INBOX_STATES}
    )
    return {state: int(counts[state]) for state in INBOX_STATES}


def get_document(*, workspace: Workspace, document_id: UUID) -> Document:
    document = Document.objects.filter(workspace=workspace, id=document_id).first()
    if document is None:
        raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
    return document


def document_summary_out(document: Document) -> DocumentSummaryOut:
    """A row of a document list."""
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
        # Labels and suggestions arrive in later steps.
        label_ids=[],
        inbox_state=inbox_state(document),
        open_suggestion_count=0,
    )


def document_out(document: Document) -> DocumentOut:
    """The document with its pages, proposals and tasks, as the API shows it."""
    return DocumentOut(
        **document_summary_out(document).model_dump(),
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
            )
            for task in document.tasks.order_by("deadline", "created_at", "id")
        ],
    )


def read_page(*, workspace: Workspace, document_id: UUID, index: int) -> tuple[bytes, str]:
    """The decrypted page file and its content type."""
    document = get_document(workspace=workspace, document_id=document_id)
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
        document = (
            Document.objects.select_for_update().filter(workspace=workspace, id=document_id).first()
        )
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
        if document.status != Document.Status.FAILED:
            raise ApiError(
                409,
                "document_not_failed",
                _("Dit document hoeft niet opnieuw verwerkt te worden."),
            )
        document.status = Document.Status.PROCESSING
        document.failure_code = ""
        document.save(update_fields=["status", "failure_code", "updated_at"])
        defer_processing(workspace_id=workspace.id, document_id=document.id)
    return document


def set_document_addressees(
    *, workspace: Workspace, user: User, document_id: UUID, person_ids: list[UUID]
) -> Document:
    """The document's addressees become exactly `person_ids`."""
    with transaction.atomic():
        document = (
            Document.objects.select_for_update().filter(workspace=workspace, id=document_id).first()
        )
        if document is None:
            raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
        if document.status == Document.Status.PROCESSING:
            raise ApiError(
                409,
                "document_processing",
                _("Dit document wordt nog verwerkt. Probeer het zo opnieuw."),
            )
        organise.set_addressees(document=document, person_ids=person_ids, actor=user)
    return document


def list_document_events(*, workspace: Workspace, document_id: UUID) -> list[DocumentEvent]:
    """The document's history, newest first."""
    document = get_document(workspace=workspace, document_id=document_id)
    return list(
        DocumentEvent.objects.filter(document=document)
        .select_related("actor")
        .annotate(undone=models.Exists(DocumentEvent.objects.filter(undoes=models.OuterRef("pk"))))
        .order_by("-created_at", "-id")
    )

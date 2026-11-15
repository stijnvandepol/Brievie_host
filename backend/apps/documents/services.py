from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _
from psycopg import errors as pg_errors

from apps.common import crypto
from apps.common.errors import ApiError
from apps.documents.intake import page_aad
from apps.documents.models import Document, DocumentPage, Folder
from apps.documents.schemas import DocumentOut, PageOut, ProposalOut, TaskOut
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


def list_documents(workspace: Workspace) -> list[Document]:
    """The newest documents of the workspace (at most 200; paging comes later)."""
    return list(Document.objects.filter(workspace=workspace).order_by("-created_at", "-id")[:200])


def get_document(*, workspace: Workspace, document_id: UUID) -> Document:
    document = Document.objects.filter(workspace=workspace, id=document_id).first()
    if document is None:
        raise ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))
    return document


def document_out(document: Document) -> DocumentOut:
    """The document with its pages, proposals and tasks, as the API shows it."""
    return DocumentOut(
        id=document.id,
        client_id=document.client_id,
        status=document.status,
        page_count=document.page_count,
        created_at=document.created_at,
        sender=document.sender,
        document_type=document.document_type,
        letter_date=document.letter_date,
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

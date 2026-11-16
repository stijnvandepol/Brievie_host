from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from ninja import File, Form, Router, Status
from ninja.files import UploadedFile
from pydantic import ValidationError

from apps.accounts.services import current_user
from apps.common.errors import ApiError
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.documents import intake, review, services
from apps.documents.models import DocumentEvent, Folder
from apps.documents.schemas import (
    AddresseesIn,
    ConfirmIn,
    DocumentEventOut,
    DocumentOut,
    DocumentSummaryOut,
    FolderIn,
    FolderOut,
    InboxCountsOut,
    LabelsIn,
    UploadMetadataIn,
)
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["documents"])


@router.get(
    "/workspaces/{workspace_id}/folders",
    response={200: list[FolderOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listFolders",
)
def list_folders(request: HttpRequest, workspace_id: UUID) -> list[Folder]:
    membership = workspace_member(request, workspace_id)
    return services.list_folders(membership.workspace)


@router.post(
    "/workspaces/{workspace_id}/folders",
    response={201: FolderOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="createFolder",
)
def create_folder(request: HttpRequest, workspace_id: UUID, payload: FolderIn) -> Status[Folder]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    folder = services.create_folder(
        workspace=membership.workspace, name=payload.name, parent_id=payload.parent_id
    )
    return Status(201, folder)


@router.post(
    "/workspaces/{workspace_id}/documents",
    response={
        201: DocumentOut,
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        411: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="createDocument",
    description=(
        "Upload a scanned document as multipart/form-data: `metadata` is a JSON string "
        "(schema `UploadMetadataIn`), followed by one `page` file (image/jpeg) per page "
        "in page order. Repeating an upload with the same `client_id` and pages answers "
        "200 with the existing document."
    ),
)
def create_document(
    request: HttpRequest,
    workspace_id: UUID,
    metadata: Form[str],
    page: File[list[UploadedFile]],
) -> Status[DocumentOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    try:
        parsed = UploadMetadataIn.model_validate_json(metadata)
    except ValidationError:
        # The error text would repeat the input (page text): not logged, not returned.
        raise ApiError(
            422, "validation_error", _("De gegevens bij deze upload kloppen niet.")
        ) from None
    document, created = intake.create_document_from_upload(
        workspace=membership.workspace,
        user=current_user(request),
        metadata=parsed,
        pages=[file.read() for file in page],
    )
    return Status(201 if created else 200, services.document_out(document))


@router.get(
    "/workspaces/{workspace_id}/documents",
    response={200: list[DocumentSummaryOut], 404: ErrorOut, 422: ErrorOut, **AUTH_ERRORS},
    operation_id="listDocuments",
    description=(
        "The newest 200 documents. `inbox_state` (`new`, `review` or `unassigned`), "
        "`person_id` and `label_id` narrow the list; together they all apply."
    ),
)
def list_documents(
    request: HttpRequest,
    workspace_id: UUID,
    inbox_state: str = "",
    person_id: UUID | None = None,
    label_id: UUID | None = None,
) -> list[DocumentSummaryOut]:
    membership = workspace_member(request, workspace_id)
    documents = services.list_documents(
        membership.workspace, inbox_state=inbox_state, person_id=person_id, label_id=label_id
    )
    return [services.document_summary_out(document) for document in documents]


@router.get(
    "/workspaces/{workspace_id}/inbox/counts",
    response={200: InboxCountsOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getInboxCounts",
)
def get_inbox_counts(request: HttpRequest, workspace_id: UUID) -> InboxCountsOut:
    membership = workspace_member(request, workspace_id)
    return InboxCountsOut(**services.inbox_counts(membership.workspace))


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}",
    response={200: DocumentOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getDocument",
)
def get_document(request: HttpRequest, workspace_id: UUID, document_id: UUID) -> DocumentOut:
    membership = workspace_member(request, workspace_id)
    document = services.get_document(workspace=membership.workspace, document_id=document_id)
    return services.document_out(document)


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}/pages/{index}",
    response={404: ErrorOut, **AUTH_ERRORS},
    operation_id="getDocumentPage",
    openapi_extra={
        "responses": {
            200: {
                "description": "The page image",
                "content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}},
            }
        }
    },
)
def get_document_page(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, index: int
) -> HttpResponse:
    membership = workspace_member(request, workspace_id)
    data, content_type = services.read_page(
        workspace=membership.workspace, document_id=document_id, index=index
    )
    response = HttpResponse(data, content_type=content_type)
    response["Cache-Control"] = "private, no-store"
    return response


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/retry",
    response={202: DocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="retryDocumentProcessing",
)
def retry_document_processing(
    request: HttpRequest, workspace_id: UUID, document_id: UUID
) -> Status[DocumentOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.retry_processing(workspace=membership.workspace, document_id=document_id)
    return Status(202, services.document_out(document))


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/confirm",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="confirmDocument",
)
def confirm_document(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, payload: ConfirmIn
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = review.confirm_document(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        data=payload,
    )
    return services.document_out(document)


@router.put(
    "/workspaces/{workspace_id}/documents/{document_id}/addressees",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="setDocumentAddressees",
)
def set_document_addressees(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, payload: AddresseesIn
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.set_document_addressees(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        person_ids=payload.person_ids,
    )
    return services.document_out(document)


@router.put(
    "/workspaces/{workspace_id}/documents/{document_id}/labels",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="setDocumentLabels",
)
def set_document_labels(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, payload: LabelsIn
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.set_document_labels(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        label_ids=payload.label_ids,
    )
    return services.document_out(document)


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}/events",
    response={200: list[DocumentEventOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listDocumentEvents",
)
def list_document_events(
    request: HttpRequest, workspace_id: UUID, document_id: UUID
) -> list[DocumentEvent]:
    membership = workspace_member(request, workspace_id)
    return services.list_document_events(workspace=membership.workspace, document_id=document_id)

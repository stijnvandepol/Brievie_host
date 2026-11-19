from uuid import UUID

from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from ninja import File, Form, Router, Status
from ninja.files import UploadedFile
from pydantic import ValidationError

from apps.accounts.auth import session_auth
from apps.accounts.services import current_user
from apps.audit.services import actor_from_request
from apps.common.errors import ApiError
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import CallerThrottle
from apps.documents import intake, member_export, review, services
from apps.documents.models import DocumentEvent, Folder, MemberExport
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
    MyExportOut,
    UploadMetadataIn,
)
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["documents"])

_MY_EXPORT = "/workspaces/{workspace_id}/my-export"
WEB_SESSION_ONLY = "Web session only: a bearer token answers 401 not_authenticated."


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


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/reopen",
    response={200: DocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    description="Takes back the AI's confirmation: the document returns to review, the confirmed"
    " fields are emptied and the tasks the AI made that are still open are removed."
    " 409 document_not_reopenable unless the AI confirmed the document.",
    operation_id="reopenDocument",
)
def reopen_document(request: HttpRequest, workspace_id: UUID, document_id: UUID) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.reopen_document(
        workspace=membership.workspace, user=current_user(request), document_id=document_id
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


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/suggestions/{suggestion_id}/accept",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        422: ErrorOut,
        **SESSION_WRITE_ERRORS,
    },
    operation_id="acceptSuggestion",
    description="Make the proposed change. Accepting an accepted proposal changes nothing.",
)
def accept_suggestion(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, suggestion_id: UUID
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.accept_suggestion(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        suggestion_id=suggestion_id,
    )
    return services.document_out(document)


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/suggestions/{suggestion_id}/dismiss",
    response={200: DocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="dismissSuggestion",
    description=(
        "Say no to a proposal; the document does not change and nothing automatic "
        "proposes or makes this change on it again."
    ),
)
def dismiss_suggestion(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, suggestion_id: UUID
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.dismiss_suggestion(
        workspace=membership.workspace, document_id=document_id, suggestion_id=suggestion_id
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


@router.post(
    "/workspaces/{workspace_id}/documents/{document_id}/events/{event_id}/undo",
    response={200: DocumentOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="undoDocumentEvent",
    description=(
        "Reverse a change a rule made (`can_undo`); the rule does not make it again on "
        "this document. 409 `event_not_undoable` when the change is not a rule's, was "
        "undone already or is gone anyway."
    ),
)
def undo_document_event(
    request: HttpRequest, workspace_id: UUID, document_id: UUID, event_id: UUID
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = services.undo_document_event(
        workspace=membership.workspace,
        user=current_user(request),
        document_id=document_id,
        event_id=event_id,
    )
    return services.document_out(document)


def _my_export_out(membership: Membership, export: MemberExport | None) -> MyExportOut:
    allowed = membership.workspace.allow_member_export
    if export is None:
        return MyExportOut(allowed=allowed, status="none")
    return MyExportOut(
        allowed=allowed,
        status=export.status,
        id=export.id,
        created_at=export.created_at,
        expires_at=export.expires_at,
        document_count=export.document_count,
        size=export.size,
    )


@router.post(
    _MY_EXPORT,
    response={
        202: MyExportOut,
        404: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
    },
    auth=session_auth,
    throttle=CallerThrottle("member_export", "5/h"),
    operation_id="createMyExport",
    description=WEB_SESSION_ONLY
    + " Any member asks for an export of their own post: the letters on their name, as a"
    " ZIP of the page images and their data. A job builds it; poll `getMyExport`. The file"
    " can be downloaded for 24 hours, by the member who asked for it only. A newer request"
    " replaces an older export. 403 `export_not_allowed` when an admin switched exports off"
    " in this workspace, 409 `export_running` while the last one is being built, 429 after"
    " five requests in an hour. Written to the log (`export_created`).",
)
def create_my_export(request: HttpRequest, workspace_id: UUID) -> Status[MyExportOut]:
    membership = workspace_member(request, workspace_id)
    export = member_export.create_my_export(
        membership=membership, actor=actor_from_request(request)
    )
    return Status(202, _my_export_out(membership, export))


@router.get(
    _MY_EXPORT,
    response={200: MyExportOut, 404: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    operation_id="getMyExport",
    description=WEB_SESSION_ONLY
    + " The caller's own export: `none`, `pending`, `ready` or `failed`, and whether the"
    " workspace allows exporting. Never another member's.",
)
def get_my_export(request: HttpRequest, workspace_id: UUID) -> MyExportOut:
    membership = workspace_member(request, workspace_id)
    return _my_export_out(membership, member_export.my_export(membership))


@router.get(
    f"{_MY_EXPORT}/file",
    response={403: ErrorOut, 404: ErrorOut, 429: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    throttle=CallerThrottle("member_export_file", "20/h"),
    operation_id="downloadMyExport",
    openapi_extra={
        "responses": {
            200: {
                "description": "The export as a ZIP file, streamed",
                "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
            }
        }
    },
    description=WEB_SESSION_ONLY
    + " The caller's own finished export as a download (`Cache-Control: private, no-store`,"
    " `Content-Disposition: attachment`). 404 `export_not_found` for none, an expired or"
    " unfinished one, and for anybody else's: an admin cannot download a member's export."
    " 403 `export_not_allowed` when exports were switched off since. Written to the log"
    " (`export_downloaded`).",
)
def download_my_export(request: HttpRequest, workspace_id: UUID) -> StreamingHttpResponse:
    membership = workspace_member(request, workspace_id)
    # Everything that needs the request happens in here; the stream needs nothing.
    chunks = member_export.open_my_export(membership=membership, actor=actor_from_request(request))
    response = StreamingHttpResponse(chunks, content_type="application/zip")
    name = f"brievie-mijn-post-{timezone.localdate().isoformat()}.zip"
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    response["Cache-Control"] = "private, no-store"
    return response

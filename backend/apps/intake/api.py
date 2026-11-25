"""Adding a PDF or photo from a browser, and downloading an uploaded original (intake
spec S 2). Web session only (DECISIONS 2026-11-25): on the public route both are closed."""

from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.http import content_disposition_header
from django.utils.translation import gettext as _
from ninja import File, Form, Router, Status
from ninja.files import UploadedFile
from pydantic import ValidationError

from apps.accounts.auth import session_auth
from apps.accounts.services import current_user
from apps.audit.services import actor_from_request
from apps.common.errors import ApiError
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.documents import services as document_services
from apps.documents.models import Document
from apps.documents.schemas import DocumentOut, FileMetadataIn
from apps.documents.upload_limits import check_file_upload_limits
from apps.intake import services
from apps.intake.files import FileRefusedError
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["documents"])

WEB_SESSION_ONLY = "Web session only: a bearer token answers 401 not_authenticated."


def refusal(error: FileRefusedError) -> ApiError:
    """The Dutch sentence for a refused file (intake spec S 2); it names no value of it."""
    if error.code == "file_too_large":
        return ApiError(
            413,
            "file_too_large",
            _("Dit bestand is groter dan %(size)s MB.")
            % {"size": settings.BRIEVIE_MAX_UPLOAD_BYTES // (1024 * 1024)},
        )
    messages = {
        "file_type_not_supported": _(
            "Dit bestand kan Brievie niet lezen. Gebruik een PDF, JPG of PNG."
        ),
        "file_unreadable": _("Dit bestand kan Brievie niet openen. Misschien is het beschadigd."),
        "too_many_pages": _("Deze PDF heeft meer dan %(count)s pagina's.")
        % {"count": settings.BRIEVIE_MAX_PAGES},
        "file_encrypted": _(
            "Deze PDF is beveiligd met een wachtwoord. "
            "Haal de beveiliging eraf en probeer het opnieuw."
        ),
        "image_too_large": _("Deze afbeelding is te groot. Maak een kleinere foto of scan."),
    }
    return ApiError(422, error.code, messages[error.code])


@router.post(
    "/workspaces/{workspace_id}/documents/files",
    response={
        201: DocumentOut,
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        411: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    operation_id="uploadDocumentFile",
    description=WEB_SESSION_ONLY
    + " Add a PDF, JPEG or PNG as one document (multipart/form-data): `metadata` is a JSON"
    " string (schema `FileMetadataIn`: `client_id` and, for an own document, the archive"
    " fields of `UploadMetadataIn`), `file` the file. The kind is read from the content."
    " The server keeps the original sealed and makes the pages in the background; the"
    " document answers `processing` with no pages yet. The same `client_id` and file"
    " again answers 200 with the existing document, another file 409 `document_conflict`."
    " 422 `file_type_not_supported`, `file_unreadable`, `too_many_pages`, `file_encrypted`,"
    " `image_too_large`; 413 `file_too_large`; 429 `too_many_uploads` (lower limits of its"
    " own, and those shared with `createDocument`). A read-only member gets 403.",
)
def upload_document_file(
    request: HttpRequest,
    workspace_id: UUID,
    metadata: Form[str],
    file: File[UploadedFile],
) -> Status[DocumentOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    user = current_user(request)
    check_file_upload_limits(request, workspace_id=workspace_id, user_id=user.id)
    try:
        parsed = FileMetadataIn.model_validate_json(metadata)
    except ValidationError:
        raise ApiError(
            422, "validation_error", _("De gegevens bij deze upload kloppen niet.")
        ) from None
    if file.size is not None and file.size > settings.BRIEVIE_MAX_UPLOAD_BYTES:
        raise refusal(FileRefusedError("file_too_large"))
    try:
        document, created = services.create_document_from_file(
            workspace=membership.workspace,
            data=file.read(),
            metadata=parsed,
            source=Document.Source.UPLOAD,
            scanned_by=user,
            original_name=file.name or "",
            actor=actor_from_request(request),
        )
    except FileRefusedError as error:
        raise refusal(error) from None
    return Status(201 if created else 200, document_services.document_out(document))


@router.get(
    "/workspaces/{workspace_id}/documents/{document_id}/original",
    response={404: ErrorOut, **AUTH_ERRORS},
    auth=session_auth,
    operation_id="downloadDocumentOriginal",
    description=WEB_SESSION_ONLY
    + " The file a browser uploaded, as it came (PDF, JPEG or PNG), for a member who sees"
    " the document. 404 `original_not_found` for an app scan.",
    openapi_extra={
        "responses": {
            200: {
                "description": "The original file",
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
                },
            }
        }
    },
)
def download_document_original(
    request: HttpRequest, workspace_id: UUID, document_id: UUID
) -> HttpResponse:
    membership = workspace_member(request, workspace_id)
    data, content_type, name = services.read_original(
        membership=membership, document_id=document_id
    )
    response = HttpResponse(data, content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(True, name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response

"""Taking in an uploaded document: check it, store it encrypted, queue the pipeline."""

import hashlib
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common import crypto
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents.models import Document, DocumentPage
from apps.documents.schemas import UploadMetadataIn
from apps.documents.storage import default_storage
from apps.pipeline.jobs import defer_processing
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Workspace

_JPEG_MAGIC = b"\xff\xd8\xff"
PAGE_SEPARATOR = "\n\f\n"


def page_path(workspace_id: UUID, document_id: UUID, index: int) -> str:
    return f"{workspace_id}/{document_id}/{index}.page"


def page_aad(workspace_id: UUID, document_id: UUID, index: int) -> bytes:
    """Binds an encrypted page file to its place: it does not open anywhere else."""
    return f"{workspace_id}/{document_id}/{index}".encode()


def create_document_from_upload(
    *, workspace: Workspace, user: User, metadata: UploadMetadataIn, pages: list[bytes]
) -> tuple[Document, bool]:
    """Store an uploaded document; `True` when it is new.

    Repeating an upload (same `client_id`, same pages) returns the existing
    document, so a client may retry after a lost answer. Needs the workspace's RLS
    context (see workspaces.deps).
    """
    _validate(metadata, pages)
    hashes = [page.sha256 for page in metadata.pages]

    existing = _find_existing(workspace, metadata.client_id)
    if existing is not None:
        return _same_upload(existing, hashes), False

    with transaction.atomic():
        try:
            with transaction.atomic():
                document = Document.objects.create(
                    workspace=workspace,
                    client_id=metadata.client_id,
                    scanned_by=user,
                    page_count=len(pages),
                    ocr_text=PAGE_SEPARATOR.join(mask_bsns(page.text) for page in metadata.pages),
                )
        except IntegrityError:
            # Another request with this client_id got in first; nothing is written yet.
            winner = Document.objects.filter(
                workspace=workspace, client_id=metadata.client_id
            ).first()
            if winner is None:
                raise
            return _same_upload(winner, hashes), False
        _store_pages(document, metadata, pages)
        defer_processing(workspace_id=workspace.id, document_id=document.id)
    return document, True


def _find_existing(workspace: Workspace, client_id: UUID) -> Document | None:
    return Document.objects.filter(workspace=workspace, client_id=client_id).first()


def _same_upload(existing: Document, hashes: list[str]) -> Document:
    stored = list(existing.pages.order_by("index").values_list("sha256", flat=True))
    if stored != hashes:
        raise ApiError(
            409,
            "document_conflict",
            _("Er bestaat al een ander document met dit kenmerk."),
        )
    return existing


def _store_pages(document: Document, metadata: UploadMetadataIn, pages: list[bytes]) -> None:
    storage = default_storage()
    key = workspace_key(document.workspace_id)
    written: list[str] = []
    try:
        for index, (data, meta) in enumerate(zip(pages, metadata.pages, strict=True), start=1):
            path = page_path(document.workspace_id, document.id, index)
            aad = page_aad(document.workspace_id, document.id, index)
            storage.write(path, crypto.seal(key, data, aad=aad))
            written.append(path)
            DocumentPage.objects.create(
                workspace_id=document.workspace_id,
                document=document,
                index=index,
                size=len(data),
                sha256=meta.sha256,
                ocr_text=mask_bsns(meta.text),
                storage_path=path,
            )
    except BaseException:
        # The rows roll back with the transaction; the files have to go by hand.
        for path in written:
            storage.delete(path)
        raise


def _validate(metadata: UploadMetadataIn, pages: list[bytes]) -> None:
    if not pages or len(pages) != len(metadata.pages):
        raise _invalid(_("Het aantal pagina's klopt niet."))
    if len(pages) > settings.BRIEVIE_MAX_PAGES:
        raise _invalid(
            _("Je kunt maximaal %(count)s pagina's in één keer sturen.")
            % {"count": settings.BRIEVIE_MAX_PAGES}
        )
    for data, meta in zip(pages, metadata.pages, strict=True):
        if len(meta.text) > settings.BRIEVIE_MAX_PAGE_TEXT_CHARS:
            raise _invalid(_("De tekst van een pagina is te lang."))
        if not data.startswith(_JPEG_MAGIC):
            raise _invalid(_("Een pagina is geen JPEG-afbeelding."))
        if hashlib.sha256(data).hexdigest() != meta.sha256:
            raise ApiError(
                422,
                "page_checksum_mismatch",
                _("Een pagina is onderweg beschadigd. Probeer het opnieuw."),
            )


def _invalid(message: str) -> ApiError:
    return ApiError(422, "validation_error", message)

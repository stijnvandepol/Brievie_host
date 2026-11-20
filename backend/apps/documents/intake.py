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
from apps.documents.jpeg import jpeg_size
from apps.documents.models import Document, DocumentPage
from apps.documents.schemas import UploadMetadataIn
from apps.documents.storage import DocumentStorage, default_storage
from apps.pipeline.jobs import defer_processing
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Workspace

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

    storage = default_storage()
    written: list[str] = []
    try:
        with transaction.atomic():
            try:
                with transaction.atomic():
                    document = Document.objects.create(
                        workspace=workspace,
                        client_id=metadata.client_id,
                        scanned_by=user,
                        page_count=len(pages),
                        ocr_text=PAGE_SEPARATOR.join(
                            mask_bsns(page.text) for page in metadata.pages
                        ),
                    )
            except IntegrityError:
                # Another request with this client_id got in first; nothing is written yet.
                winner = Document.objects.filter(
                    workspace=workspace, client_id=metadata.client_id
                ).first()
                if winner is None:
                    raise
                return _same_upload(winner, hashes), False
            _store_pages(document, metadata, pages, storage, written)
            defer_processing(workspace_id=workspace.id, document_id=document.id)
    except BaseException:
        # Anything that fails after a page was written (another page, queueing the
        # job) rolls the rows back; the files have to go by hand, or encrypted
        # orphans stay behind. Queueing is part of the transaction (Procrastinate
        # writes its job row in it), so nothing here has to wait for the commit.
        for path in written:
            storage.delete(path)
        raise
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


def _store_pages(
    document: Document,
    metadata: UploadMetadataIn,
    pages: list[bytes],
    storage: DocumentStorage,
    written: list[str],
) -> None:
    """Write the encrypted pages and their rows; every written path goes into `written`."""
    key = workspace_key(document.workspace_id)
    for index, (data, meta) in enumerate(zip(pages, metadata.pages, strict=True), start=1):
        path = page_path(document.workspace_id, document.id, index)
        aad = page_aad(document.workspace_id, document.id, index)
        # Recorded first: a write that fails halfway must be cleaned up too.
        written.append(path)
        storage.write(path, crypto.seal(key, data, aad=aad))
        DocumentPage.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            index=index,
            size=len(data),
            sha256=meta.sha256,
            ocr_text=mask_bsns(meta.text),
            storage_path=path,
        )


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
        if jpeg_size(data) is None:
            # Before anything is stored or queued: no AI call for a page that is no picture.
            raise _invalid(_("Een pagina is geen bruikbare JPEG-afbeelding."))
        if hashlib.sha256(data).hexdigest() != meta.sha256:
            raise ApiError(
                422,
                "page_checksum_mismatch",
                _("Een pagina is onderweg beschadigd. Probeer het opnieuw."),
            )


def _invalid(message: str) -> ApiError:
    return ApiError(422, "validation_error", message)

"""Taking in an uploaded document: check it, store it encrypted, queue the pipeline."""

import hashlib
from collections.abc import Sequence
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.common import crypto
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents import own, reading, visibility
from apps.documents.jpeg import jpeg_size
from apps.documents.models import Document, DocumentPage
from apps.documents.schemas import UploadMetadataIn
from apps.documents.storage import DocumentStorage, default_storage
from apps.pipeline.jobs import defer_processing
from apps.workspaces.context import system_actor
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Workspace

PAGE_SEPARATOR = "\n\f\n"


def page_path(workspace_id: UUID, document_id: UUID, index: int) -> str:
    return f"{workspace_id}/{document_id}/{index}.page"


def page_aad(workspace_id: UUID, document_id: UUID, index: int) -> bytes:
    """Binds an encrypted page file to its place: it does not open anywhere else."""
    return f"{workspace_id}/{document_id}/{index}".encode()


def original_path(workspace_id: UUID, document_id: UUID) -> str:
    return f"{workspace_id}/{document_id}/original"


def original_aad(workspace_id: UUID, document_id: UUID) -> bytes:
    """Binds the sealed original to its place; never equal to a page's AAD."""
    return f"{workspace_id}/{document_id}/original".encode()


def create_document_from_upload(
    *,
    workspace: Workspace,
    user: User,
    metadata: UploadMetadataIn,
    pages: list[bytes],
    actor: AuditActor | None = None,
) -> tuple[Document, bool]:
    """Store an uploaded document; `True` when it is new.

    Repeating an upload (same `client_id`, same pages) returns the existing
    document, so a client may retry after a lost answer. Needs the workspace's RLS
    context (see workspaces.deps). With `kind = document` it is an own document
    (`apps.documents.own`): checked before anything is stored, organised in the same
    transaction. `actor` is who the audit log names for a private flip (the uploader's
    browser or device; the uploader when absent).
    """
    _validate(metadata, pages)
    own_data = own.validate_fields(metadata, today=timezone.localdate())
    hashes = [page.sha256 for page in metadata.pages]

    existing = _find_existing(workspace, metadata.client_id)
    if existing is not None:
        return _same_upload(existing, hashes), False

    storage = default_storage()
    written: list[str] = []
    try:
        with transaction.atomic():
            if own_data is not None:
                # The membership first (the one lock order), then the names are looked up.
                own.lock_creator(workspace, user)
                own.check_names(workspace, own_data)
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
                        **(own.document_fields(own_data) if own_data is not None else {}),
                    )
            except IntegrityError:
                # Another request with this client_id got in first; nothing is written yet.
                with system_actor():
                    winner = Document.objects.filter(
                        workspace=workspace, client_id=metadata.client_id
                    ).first()
                if winner is None:
                    raise
                return _same_upload(winner, hashes), False
            _store_pages(document, metadata, pages, storage, written)
            if own_data is not None:
                own.create_document(
                    document,
                    own_data,
                    user=user,
                    actor=actor if actor is not None else AuditActor.for_user(user),
                )
            # The uploader made it: not unread for them (navigation spec S 5.3).
            reading.mark_scanned(document, user)
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
    """As the system: the client_id is unique in the workspace, also among the letters
    the uploader no longer sees (a letter they scanned and handed over)."""
    with system_actor():
        return Document.objects.filter(workspace=workspace, client_id=client_id).first()


def _same_upload(existing: Document, hashes: list[str]) -> Document:
    """The existing letter when the uploader may read it and the pages are the same;
    otherwise the same 409 as for a different upload, so the answer tells nothing. A
    letter in the trash is never brought back by an upload: 409 `document_trashed`, only
    once the pages and the right to read it are established."""
    with system_actor():
        stored = list(existing.pages.order_by("index").values_list("sha256", flat=True))
    return existing_or_conflict(existing, same=stored == hashes)


def existing_or_conflict(existing: Document, *, same: bool) -> Document:
    """The repeated upload's answer once the caller compared the content (`same`): the
    existing document, or 409 (`document_conflict`, `document_trashed`)."""
    if not same or not visibility.can_read(existing.id):
        raise ApiError(
            409,
            "document_conflict",
            _("Er bestaat al een ander document met dit kenmerk."),
        )
    if existing.purge_started_at is not None:
        # Restoring it is no longer possible: no advice to do so.
        raise ApiError(
            409,
            "document_trashed",
            _("Deze brief staat in de prullenbak en wordt nu voorgoed gewist."),
        )
    if existing.trashed_at is not None:
        raise ApiError(
            409,
            "document_trashed",
            _("Deze brief staat in de prullenbak. Zet hem daar terug als je hem weer wilt zien."),
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
    write_pages(
        document,
        [
            (data, meta.sha256, mask_bsns(meta.text))
            for data, meta in zip(pages, metadata.pages, strict=True)
        ],
        storage,
        written,
    )


def write_pages(
    document: Document,
    pages: Sequence[tuple[bytes, str, str]],
    storage: DocumentStorage,
    written: list[str],
) -> None:
    """Seal and write `(jpeg, sha256, masked_text)` per page, with its `DocumentPage` row,
    from index 1; every written path goes into `written` first (the caller removes them
    when its transaction fails). The text must already be BSN-masked."""
    key = workspace_key(document.workspace_id)
    for index, (data, sha256, masked_text) in enumerate(pages, start=1):
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
            sha256=sha256,
            ocr_text=masked_text,
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

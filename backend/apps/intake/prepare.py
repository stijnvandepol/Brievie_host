"""The pipeline's first step for an uploaded file: render its pages and text (intake
spec S 1.1). Reading, rendering and OCR happen outside any transaction; the pages are
written in one short transaction under the document's row lock, only while it is still
`processing` without pages. A retry after the pages exist does nothing here."""

import hashlib
from uuid import UUID

from django.db import transaction

from apps.common import crypto
from apps.common.privacy import mask_bsns
from apps.documents.intake import PAGE_SEPARATOR, original_aad, original_path, write_pages
from apps.documents.models import LIVE, Document
from apps.documents.storage import DocumentStorage, default_storage
from apps.intake.files import FileKind
from apps.intake.ocr import OcrEngine, ocr_engine
from apps.intake.render import render_pages
from apps.pipeline.errors import PermanentError
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.keys import workspace_key


def _needs_pages(document_id: UUID, *, lock: bool) -> Document | None:
    documents = Document.objects.select_for_update(no_key=True) if lock else Document.objects
    return (
        documents.filter(LIVE, id=document_id, status=Document.Status.PROCESSING, page_count=0)
        .exclude(original_kind=Document.OriginalKind.NONE)
        .first()
    )


def prepare_document(
    workspace_id: UUID,
    document_id: UUID,
    *,
    ocr: OcrEngine | None = None,
    storage: DocumentStorage | None = None,
) -> None:
    storage = storage or default_storage()
    with workspace_context(workspace_id, actor=SYSTEM):
        document = _needs_pages(document_id, lock=False)
        if document is None:
            return
        kind = FileKind(document.original_kind)
        expected = document.original_sha256
    try:
        data = crypto.open_sealed(
            workspace_key(workspace_id),
            storage.read(original_path(workspace_id, document_id)),
            aad=original_aad(workspace_id, document_id),
        )
    except (OSError, crypto.DecryptionError):
        raise PermanentError("file_unreadable") from None
    if hashlib.sha256(data).hexdigest() != expected:
        raise PermanentError("file_unreadable")
    rendered = render_pages(data, kind, ocr or ocr_engine())
    pages = [
        (page.jpeg, hashlib.sha256(page.jpeg).hexdigest(), mask_bsns(page.text))
        for page in rendered
    ]

    written: list[str] = []
    try:
        with workspace_context(workspace_id, actor=SYSTEM), transaction.atomic():
            document = _needs_pages(document_id, lock=True)
            if document is None:
                # Trashed, finished or given pages meanwhile: nothing to add.
                return
            write_pages(document, pages, storage, written)
            document.page_count = len(pages)
            document.ocr_text = PAGE_SEPARATOR.join(text for _jpeg, _sha, text in pages)
            document.save(update_fields=["page_count", "ocr_text", "updated_at"])
    except BaseException:
        for path in written:
            storage.delete(path)
        raise

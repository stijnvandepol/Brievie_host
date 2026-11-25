"""The pipeline's first step for an uploaded file (intake plan Task 5)."""

import hashlib
import uuid

import pytest

from apps.common import crypto
from apps.documents.intake import PAGE_SEPARATOR, original_aad, original_path, page_aad
from apps.documents.models import Document, DocumentPage
from apps.documents.storage import default_storage
from apps.intake.prepare import prepare_document
from apps.pipeline import jobs
from apps.pipeline.errors import RetryableError
from apps.pipeline.providers import ProviderUnavailable
from apps.pipeline.steps import run_pipeline
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Workspace
from tests.intake_fixtures import FakeOcr, make_encrypted_pdf, make_pdf
from tests.test_pipeline_steps import TODAY, FakeProvider, answer

LAYER = "Gemeente Voorbeeldstad, aanslag afvalstoffenheffing, betaal voor 10 december 2026"
NAME = "rekening-voorbeeldnaam.pdf"


def _uploaded(ws: Workspace, data: bytes, kind: str = "pdf", **fields: object) -> Document:
    """A processing document with this original, as `create_document_from_file` leaves it
    (without its checks: a file that later turns out bad must fail in the worker)."""
    with workspace_context(ws.id, actor=SYSTEM):
        document = Document.objects.create(
            workspace=ws,
            client_id=uuid.uuid4(),
            page_count=0,
            source=Document.Source.UPLOAD,
            original_kind=kind,
            original_sha256=hashlib.sha256(data).hexdigest(),
            original_size=len(data),
            original_name=NAME,
            **fields,
        )
    default_storage().write(
        original_path(ws.id, document.id),
        crypto.seal(workspace_key(ws.id), data, aad=original_aad(ws.id, document.id)),
    )
    return document


def _runner(ocr: FakeOcr, provider: FakeProvider | None = None) -> jobs.Runner:
    def run(workspace_id: uuid.UUID, document_id: uuid.UUID) -> None:
        prepare_document(workspace_id, document_id, ocr=ocr)
        run_pipeline(
            workspace_id,
            document_id,
            provider_factory=lambda _: provider,
            today=lambda: TODAY,
        )

    return run


def _fresh(ws: Workspace, document: Document) -> tuple[Document, list[DocumentPage]]:
    with workspace_context(ws.id, actor=SYSTEM):
        fresh = Document.objects.get(id=document.id)
        return fresh, list(fresh.pages.order_by("index"))


def test_an_uploaded_pdf_gets_its_pages_and_text(ws_a: Workspace) -> None:
    document = _uploaded(ws_a, make_pdf([LAYER, None]))
    ocr = FakeOcr("Tweede pagina gelezen")

    jobs.run_job(ws_a.id, document.id, attempts=0, run=_runner(ocr))

    fresh, pages = _fresh(ws_a, document)
    assert fresh.status == Document.Status.NEEDS_REVIEW
    assert fresh.page_count == 2
    assert [page.index for page in pages] == [1, 2]
    key = workspace_key(ws_a.id)
    for page in pages:
        sealed = default_storage().read(page.storage_path)
        plain = crypto.open_sealed(key, sealed, aad=page_aad(ws_a.id, document.id, page.index))
        assert hashlib.sha256(plain).hexdigest() == page.sha256
    assert "Voorbeeldstad" in pages[0].ocr_text
    assert pages[1].ocr_text == "Tweede pagina gelezen"
    assert fresh.ocr_text == PAGE_SEPARATOR.join(page.ocr_text for page in pages)
    assert len(ocr.calls) == 1


def test_a_bsn_is_masked_in_page_and_document_text(ws_a: Workspace) -> None:
    document = _uploaded(ws_a, make_pdf([None]))

    jobs.run_job(ws_a.id, document.id, attempts=0, run=_runner(FakeOcr("BSN 111222333 staat hier")))

    fresh, pages = _fresh(ws_a, document)
    assert "111222333" not in fresh.ocr_text
    assert "[BSN]" in pages[0].ocr_text
    assert "111222333" not in pages[0].ocr_text


def test_the_ai_never_sees_the_file_name(ws_a: Workspace) -> None:
    document = _uploaded(ws_a, make_pdf([LAYER]))
    provider = FakeProvider(answer())

    jobs.run_job(ws_a.id, document.id, attempts=0, run=_runner(FakeOcr(), provider))

    assert len(provider.requests) == 1
    assert "Voorbeeldstad" in provider.texts[0]
    assert "voorbeeldnaam" not in repr(provider.requests[0])


def test_a_changed_original_fails_as_unreadable(ws_a: Workspace) -> None:
    document = _uploaded(ws_a, make_pdf([LAYER]))
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(original_sha256="0" * 64)

    jobs.run_job(ws_a.id, document.id, attempts=0, run=_runner(FakeOcr()))

    fresh, pages = _fresh(ws_a, document)
    assert (fresh.status, fresh.failure_code) == ("failed", "file_unreadable")
    assert pages == []


def test_an_encrypted_pdf_fails_with_its_code(ws_a: Workspace) -> None:
    document = _uploaded(ws_a, make_encrypted_pdf())

    jobs.run_job(ws_a.id, document.id, attempts=0, run=_runner(FakeOcr()))

    fresh, _pages = _fresh(ws_a, document)
    assert (fresh.status, fresh.failure_code) == ("failed", "file_encrypted")


def test_retry_after_prepare_keeps_pages(ws_a: Workspace) -> None:
    document = _uploaded(ws_a, make_pdf([None, None]))
    ocr = FakeOcr("Gelezen")
    provider = FakeProvider(ProviderUnavailable(), answer())

    with pytest.raises(RetryableError):
        jobs.run_job(ws_a.id, document.id, attempts=0, run=_runner(ocr, provider))
    jobs.run_job(ws_a.id, document.id, attempts=1, run=_runner(ocr, provider))

    fresh, pages = _fresh(ws_a, document)
    assert fresh.status == Document.Status.NEEDS_REVIEW
    assert len(pages) == 2
    assert len(ocr.calls) == 2


def test_an_app_scan_is_left_alone(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = Document.objects.create(
            workspace=ws_a, client_id=uuid.uuid4(), page_count=1, ocr_text="Brief"
        )
    ocr = FakeOcr()

    prepare_document(ws_a.id, document.id, ocr=ocr)

    fresh, pages = _fresh(ws_a, document)
    assert (fresh.page_count, fresh.ocr_text, pages, ocr.calls) == (1, "Brief", [], [])


def test_an_uploaded_own_document_ends_done_without_an_ai_call(ws_a: Workspace) -> None:
    document = _uploaded(ws_a, make_pdf([LAYER]))
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(
            kind=Document.Kind.DOCUMENT, title="Polis inboedel", document_type=Document.Type.POLICY
        )
    provider = FakeProvider(answer())

    jobs.run_job(ws_a.id, document.id, attempts=0, run=_runner(FakeOcr(), provider))

    fresh, pages = _fresh(ws_a, document)
    assert fresh.status == Document.Status.DONE
    assert len(pages) == 1
    assert provider.requests == []

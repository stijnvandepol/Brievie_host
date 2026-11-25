"""Storing an uploaded file as a processing document (intake plan Task 4)."""

import uuid
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.common import crypto
from apps.common.errors import ApiError
from apps.documents.intake import original_aad, original_path, page_aad
from apps.documents.models import Document
from apps.documents.schemas import FileMetadataIn
from apps.documents.storage import default_storage
from apps.intake import services
from apps.intake.files import FileRefusedError
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of
from tests.intake_fixtures import make_jpeg, make_pdf


def _stored_files() -> list[Path]:
    root = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def _store(
    ws: Workspace, data: bytes, *, client_id: uuid.UUID | None = None, name: str = "brief.pdf"
) -> tuple[Document, bool]:
    with workspace_context(ws.id, actor=SYSTEM):
        return services.create_document_from_file(
            workspace=ws,
            data=data,
            metadata=FileMetadataIn(client_id=client_id or uuid.uuid4()),
            source=Document.Source.UPLOAD,
            scanned_by=admin_of(ws),
            original_name=name,
        )


def test_the_original_is_sealed_to_its_place(ws_a: Workspace) -> None:
    data = make_pdf(["Een brief van de gemeente"])
    document, created = _store(ws_a, data)
    assert created
    sealed = default_storage().read(original_path(ws_a.id, document.id))
    with workspace_context(ws_a.id, actor=SYSTEM):
        key = workspace_key(ws_a.id)
    assert crypto.open_sealed(key, sealed, aad=original_aad(ws_a.id, document.id)) == data
    with pytest.raises(crypto.DecryptionError):
        crypto.open_sealed(key, sealed, aad=page_aad(ws_a.id, document.id, 1))
    assert data not in sealed


def test_fields_and_status(ws_a: Workspace) -> None:
    data = make_jpeg(60, 40)
    document, _created = _store(ws_a, data, name="C:\\fakepath\\foto.jpg")
    with workspace_context(ws_a.id, actor=SYSTEM):
        document.refresh_from_db()
    assert document.status == Document.Status.PROCESSING
    assert document.source == Document.Source.UPLOAD
    assert document.page_count == 0
    assert document.ocr_text == ""
    assert document.original_kind == "jpeg"
    assert document.original_size == len(data)
    assert document.original_name == "foto.jpg"
    assert document.scanned_by == admin_of(ws_a)
    assert document.kind == Document.Kind.LETTER


def test_one_processing_job_is_queued(ws_a: Workspace) -> None:
    document, _created = _store(ws_a, make_pdf(["Brief"]))
    jobs = ProcrastinateJob.objects.filter(task_name="process_document")
    assert [job.args["document_id"] for job in jobs] == [str(document.id)]


def test_the_same_file_again_is_the_same_document(ws_a: Workspace) -> None:
    data = make_pdf(["Brief"])
    client_id = uuid.uuid4()
    first, _ = _store(ws_a, data, client_id=client_id)
    again, created = _store(ws_a, data, client_id=client_id)
    assert not created
    assert again.id == first.id
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Document.objects.count() == 1


def test_another_file_under_the_same_client_id_is_a_conflict(ws_a: Workspace) -> None:
    client_id = uuid.uuid4()
    _store(ws_a, make_pdf(["Brief"]), client_id=client_id)
    with pytest.raises(ApiError) as caught:
        _store(ws_a, make_pdf(["Een andere brief"]), client_id=client_id)
    assert caught.value.status == 409
    assert caught.value.code == "document_conflict"


def test_a_refused_file_stores_nothing(ws_a: Workspace) -> None:
    with pytest.raises(FileRefusedError):
        _store(ws_a, b"GIF89a" + bytes(30))
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Document.objects.count() == 0
    assert _stored_files() == []


def test_a_failed_queue_leaves_no_row_and_no_file(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(**_kwargs: object) -> None:
        raise RuntimeError("queue down")

    monkeypatch.setattr(services, "defer_processing", broken)
    with pytest.raises(RuntimeError):
        _store(ws_a, make_pdf(["Brief"]))
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Document.objects.count() == 0
    assert _stored_files() == []

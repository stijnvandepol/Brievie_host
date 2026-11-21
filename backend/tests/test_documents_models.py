import uuid
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from apps.documents.models import Document, DocumentPage, Extraction
from apps.pipeline.models import AISettings
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace


def make_document(workspace: Workspace, **fields: object) -> Document:
    return Document.objects.create(
        workspace=workspace, client_id=uuid.uuid4(), page_count=1, ocr_text="tekst", **fields
    )


def test_new_document_is_processing_with_empty_review_fields(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = make_document(ws_a)

    assert document.status == Document.Status.PROCESSING
    assert document.source == Document.Source.SCAN
    assert (document.sender, document.document_type, document.summary) == ("", "", "")
    assert document.letter_date is None
    assert document.confirmed_at is None
    assert document.failure_code == ""


def test_new_rows_are_invisible_outside_their_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = make_document(ws_a)
        DocumentPage.objects.create(
            workspace=ws_a,
            document=document,
            index=1,
            size=3,
            sha256="0" * 64,
            storage_path="a/b/1.page",
        )
        Extraction.objects.create(
            workspace=ws_a,
            document=document,
            field="sender",
            value="CAK",
            quote="CAK",
            confidence=Decimal("0.9"),
        )
        Task.objects.create(workspace=ws_a, document=document, type="pay", description="Betalen")
        AISettings.objects.create(workspace=ws_a)

    for model in (Document, DocumentPage, Extraction, Task, AISettings):
        assert model.objects.count() == 0, model
        with workspace_context(ws_b.id, actor=SYSTEM):
            assert model.objects.count() == 0, model
        with workspace_context(ws_a.id, actor=SYSTEM):
            assert model.objects.count() == 1, model


def test_client_id_is_unique_per_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    client_id = uuid.uuid4()
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.create(workspace=ws_a, client_id=client_id, page_count=1)
        with pytest.raises(IntegrityError), transaction.atomic():
            Document.objects.create(workspace=ws_a, client_id=client_id, page_count=1)
    with workspace_context(ws_b.id, actor=SYSTEM):
        Document.objects.create(workspace=ws_b, client_id=client_id, page_count=1)


def test_page_index_is_unique_per_document(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = make_document(ws_a)
        page = {"workspace": ws_a, "document": document, "index": 1, "size": 1, "sha256": "0" * 64}
        DocumentPage.objects.create(storage_path="a", **page)
        with pytest.raises(IntegrityError), transaction.atomic():
            DocumentPage.objects.create(storage_path="b", **page)


@pytest.mark.parametrize("confidence", [Decimal("-0.1"), Decimal("1.001")])
def test_confidence_must_be_between_0_and_1(ws_a: Workspace, confidence: Decimal) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = make_document(ws_a)
        with pytest.raises(IntegrityError), transaction.atomic():
            Extraction.objects.create(
                workspace=ws_a, document=document, field="sender", value="x", confidence=confidence
            )


def test_rows_cannot_point_at_a_document_of_another_workspace(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    # Foreign-key checks bypass RLS, so the models guard this themselves.
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = make_document(ws_a)

    with workspace_context(ws_b.id, actor=SYSTEM):
        with pytest.raises(ValueError, match="same workspace"):
            DocumentPage.objects.create(
                workspace=ws_b,
                document_id=document.id,
                index=1,
                size=1,
                sha256="0" * 64,
                storage_path="a",
            )
        with pytest.raises(ValueError, match="same workspace"):
            Extraction.objects.create(
                workspace=ws_b,
                document_id=document.id,
                field="sender",
                value="x",
                confidence=Decimal("0.5"),
            )
        with pytest.raises(ValueError, match="same workspace"):
            Task.objects.create(
                workspace=ws_b, document_id=document.id, type="pay", description="x"
            )
        assert DocumentPage.objects.count() == 0


def test_task_without_document_is_allowed(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        task = Task.objects.create(workspace=ws_a, type="other", description="Bellen")

    assert task.status == Task.Status.OPEN
    assert task.deadline is None
    assert task.deadline_source == Task.DeadlineSource.MANUAL


def test_ai_settings_default_to_off_and_are_unique_per_workspace(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        settings = AISettings.objects.create(workspace=ws_a)
        assert settings.mode == AISettings.Mode.OFF
        assert settings.api_key_sealed is None
        with pytest.raises(IntegrityError), transaction.atomic():
            AISettings.objects.create(workspace=ws_a)


def test_deleting_a_document_keeps_its_task(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = make_document(ws_a)
        task = Task.objects.create(workspace=ws_a, document=document, type="pay", description="x")
        document.delete()
        task.refresh_from_db()

    assert task.document_id is None

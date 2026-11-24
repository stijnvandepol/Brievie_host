"""The pipeline's document branch (archive plan Task 5, spec S 6.2, AR3, AR4): an own
document ends `done`, confirmed by its uploader; no AI call, no proposals, no rules, no
autopilot, no task; a failure ends `done` with a code and can be tried again."""

import uuid
from datetime import date

import pytest
from django.test import Client

from apps.common.errors import ApiError
from apps.documents import services
from apps.documents.models import Document, DocumentSuggestion, Extraction
from apps.labels.models import Label
from apps.pipeline.providers import LLMProvider
from apps.pipeline.steps import mark_failed, run_pipeline
from apps.rules.models import RuleRun
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.archive_support import own_document
from tests.helpers import post_json
from tests.my_tasks_support import household
from tests.rules_support import add_label, make_rule, text_word
from tests.visibility_support import as_actor

TODAY = date(2026, 11, 24)
TEXT = "Paspoort Koninkrijk der Nederlanden geldig tot 12 maart 2031 kantoor 111222333"


class _Asked:
    """A provider factory that records that it was asked; it never gives a provider."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, workspace_id: uuid.UUID) -> LLMProvider | None:
        self.calls += 1
        return None


def _stored(document: Document) -> Document:
    with as_actor(document.workspace, SYSTEM):
        return Document.objects.get(id=document.id)


def test_document_ends_done_confirmed_by_the_uploader_not_the_ai() -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.piet, status="processing", ocr_text=TEXT)
    run_pipeline(h.workspace.id, document.id, provider_factory=_Asked(), today=lambda: TODAY)
    stored = _stored(document)
    assert stored.status == Document.Status.DONE
    assert stored.confirmed_by_id == h.piet.id
    assert stored.confirmed_at is not None
    assert stored.confirmed_by_ai is False
    assert stored.failure_code == ""
    assert stored.kind == Document.Kind.DOCUMENT


def test_document_never_gets_tasks_proposals_or_an_ai_call() -> None:
    h = household()
    asked = _Asked()
    document = own_document(
        h.workspace, on=[], scanned_by=h.anne, status="processing", ocr_text=f"Piet {TEXT}"
    )
    run_pipeline(h.workspace.id, document.id, provider_factory=asked, today=lambda: TODAY)
    assert asked.calls == 0
    with as_actor(h.workspace, SYSTEM):
        assert not Task.objects.filter(document_id=document.id).exists()
        assert not DocumentSuggestion.objects.filter(document_id=document.id).exists()
        assert not Extraction.objects.filter(document_id=document.id).exists()
    # The name in the text proposes nobody: the uploader chose the person.
    assert _stored(document).addressee_ids == []


def test_rules_never_run_on_a_document() -> None:
    h = household()
    with as_actor(h.workspace, SYSTEM):
        label = Label.objects.create(workspace=h.workspace, name="Kantoorzaken")
    make_rule(h.workspace, text_word("kantoor"), actions=[add_label(label)])
    document = own_document(h.workspace, scanned_by=h.anne, status="processing", ocr_text=TEXT)
    run_pipeline(h.workspace.id, document.id, provider_factory=_Asked(), today=lambda: TODAY)
    with as_actor(h.workspace, SYSTEM):
        assert not RuleRun.objects.filter(document_id=document.id).exists()
        assert not DocumentSuggestion.objects.filter(document_id=document.id).exists()
    assert _stored(document).status == Document.Status.DONE


def test_failure_ends_done_with_failure_code_and_retry_works() -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, status="processing", ocr_text=TEXT)
    mark_failed(h.workspace.id, document.id, "internal_error")
    stored = _stored(document)
    assert (stored.status, stored.failure_code) == (Document.Status.DONE, "internal_error")
    assert stored.confirmed_by_id == h.anne.id
    with as_actor(h.workspace, h.anne.id):
        again = services.retry_processing(workspace=h.workspace, document_id=document.id)
    assert (again.status, again.failure_code) == (Document.Status.PROCESSING, "")
    run_pipeline(h.workspace.id, document.id, provider_factory=_Asked(), today=lambda: TODAY)
    assert (_stored(document).status, _stored(document).failure_code) == ("done", "")


def test_a_done_document_without_a_failure_is_not_retried() -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, status="done")
    with as_actor(h.workspace, h.anne.id), pytest.raises(ApiError) as caught:
        services.retry_processing(workspace=h.workspace, document_id=document.id)
    assert (caught.value.status, caught.value.code) == (409, "document_not_failed")


def test_confirm_document_on_a_document_is_409_not_reviewable(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, status="done")
    client.force_login(h.anne)
    response = post_json(
        client,
        f"/workspaces/{h.workspace.id}/documents/{document.id}/confirm",
        {"sender": "", "document_type": "", "summary": "", "summary_by_ai": False, "tasks": []},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "document_not_reviewable"
    stored = _stored(document)
    assert (stored.kind, stored.status, stored.sender) == ("document", "done", "")

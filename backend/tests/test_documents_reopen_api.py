"""Reopening a letter the AI confirmed: back to review, the AI's open tasks gone."""

import uuid
from datetime import date
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.documents.models import Document, DocumentEvent, DocumentLabel, DocumentSuggestion
from apps.labels.models import Label
from apps.tasks.models import Task
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of
from tests.factories import UserFactory
from tests.helpers import post_json

if TYPE_CHECKING:
    from tests.helpers import Response

pytestmark = pytest.mark.usefixtures("without_preset_rules")


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def ai_confirmed(workspace: Workspace, *, by_ai: bool = True, status: str = "done") -> Document:
    with workspace_context(workspace.id):
        document = Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            ocr_text="Brief",
            status=status,
            sender="Belastingdienst",
            document_type="assessment",
            summary="Samenvatting",
            summary_by_ai=True,
            letter_date=date(2026, 11, 3),
            confirmed_by_ai=by_ai,
            confirmed_organisation={"label_ids": [], "addressee_ids": []},
        )
        Document.objects.filter(id=document.id).update(confirmed_at="2026-11-14T10:00:00Z")
        return document


def reopen(client: Client, workspace_id: object, document_id: object) -> "Response":
    return post_json(client, f"/workspaces/{workspace_id}/documents/{document_id}/reopen")


def test_reopen_empties_the_confirmed_fields_and_removes_open_ai_tasks(
    client_a: Client, ws_a: Workspace
) -> None:
    document = ai_confirmed(ws_a)
    with workspace_context(ws_a.id):
        label = Label.objects.create(workspace=ws_a, name="Belasting")
        DocumentLabel.objects.create(workspace=ws_a, document=document, label=label)
        DocumentSuggestion.objects.create(
            workspace=ws_a,
            document=document,
            action="add_label",
            label=Label.objects.create(workspace=ws_a, name="Gezondheid"),
            origin="ai",
            confidence="0.700",
        )
        Task.objects.create(
            workspace=ws_a, document=document, type="pay", description="Open AI", created_by_ai=True
        )
        Task.objects.create(
            workspace=ws_a,
            document=document,
            type="pay",
            description="Afgevinkt",
            created_by_ai=True,
            status="done",
        )
        Task.objects.create(workspace=ws_a, document=document, type="pay", description="Eigen")

    r = reopen(client_a, ws_a.id, document.id)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_review"
    assert (body["sender"], body["document_type"], body["summary"], body["letter_date"]) == (
        "",
        "",
        "",
        None,
    )
    assert body["summary_by_ai"] is False
    with workspace_context(ws_a.id):
        after = Document.objects.get(id=document.id)
        assert (after.confirmed_at, after.confirmed_organisation, after.confirmed_by_ai) == (
            None,
            None,
            False,
        )
        assert set(Task.objects.filter(document=after).values_list("description", flat=True)) == {
            "Afgevinkt",
            "Eigen",
        }
        assert DocumentLabel.objects.filter(document=after).count() == 1
        assert DocumentSuggestion.objects.filter(document=after, state="open").count() == 1
        event = DocumentEvent.objects.get(document=after, kind="reopened")
        assert (event.origin, event.actor) == ("user", admin_of(ws_a))


def test_a_person_confirmed_document_is_not_reopenable(client_a: Client, ws_a: Workspace) -> None:
    document = ai_confirmed(ws_a, by_ai=False)

    r = reopen(client_a, ws_a.id, document.id)

    assert r.status_code == 409
    assert r.json() == {
        "code": "document_not_reopenable",
        "message": "Dit document kun je niet opnieuw controleren.",
    }


@pytest.mark.parametrize("status", ["processing", "needs_review", "failed"])
def test_a_document_that_is_not_done_is_not_reopenable(
    client_a: Client, ws_a: Workspace, status: str
) -> None:
    document = ai_confirmed(ws_a, status=status)

    r = reopen(client_a, ws_a.id, document.id)

    assert r.status_code == 409
    assert r.json()["code"] == "document_not_reopenable"


def test_an_unknown_document_is_not_found(client_a: Client, ws_a: Workspace) -> None:
    r = reopen(client_a, ws_a.id, uuid.uuid4())

    assert r.status_code == 404
    assert r.json()["code"] == "document_not_found"


def test_a_document_of_another_workspace_is_not_found(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    document = ai_confirmed(ws_b)

    assert reopen(client_a, ws_a.id, document.id).status_code == 404


def test_a_stranger_is_refused_and_the_anonymous_is_not_signed_in(
    ws_a: Workspace, client: Client
) -> None:
    document = ai_confirmed(ws_a)
    assert reopen(client, ws_a.id, document.id).status_code == 401
    client.force_login(UserFactory.create())
    assert reopen(client, ws_a.id, document.id).status_code == 404


def test_after_a_reopen_the_normal_confirm_works(client_a: Client, ws_a: Workspace) -> None:
    document = ai_confirmed(ws_a)
    assert reopen(client_a, ws_a.id, document.id).status_code == 200

    r = post_json(
        client_a,
        f"/workspaces/{ws_a.id}/documents/{document.id}/confirm",
        {
            "sender": "Belastingdienst",
            "document_type": "assessment",
            "letter_date": "2026-11-03",
            "summary": "",
            "summary_by_ai": False,
            "tasks": [],
        },
    )

    assert r.status_code == 200
    with workspace_context(ws_a.id):
        done = Document.objects.get(id=document.id)
        assert (done.status, done.confirmed_by_ai) == ("done", False)
        assert done.confirmed_by == admin_of(ws_a)

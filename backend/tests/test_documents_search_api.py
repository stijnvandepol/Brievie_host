"""POST /workspaces/{id}/documents/search (searchDocuments)."""

import json
import logging
import uuid

import pytest
from django.test import Client

from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.documents.models import Document
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, bearer_for, documents_url
from tests.factories import UserFactory

TERM = "zeldzaamwoordxyz"


def search_url(workspace_id: object) -> str:
    return f"{documents_url(workspace_id)}/search"


def letter(workspace: Workspace, text: str, *, sender: str = "") -> Document:
    with workspace_context(workspace.id):
        return Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            status="done",
            ocr_text=text,
            sender=sender,
        )


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def test_search_answers_hits_with_a_fragment(client_a: Client, ws_a: Workspace) -> None:
    found = letter(ws_a, f"Hierbij de brief over {TERM} van dit jaar.", sender="Gemeente")

    r = client_a.post(search_url(ws_a.id), {"q": TERM}, content_type="application/json")

    assert r.status_code == 200
    body = r.json()
    assert body["fuzzy"] is False
    assert body["bsn_removed"] is False
    assert body["next_offset"] is None
    (item,) = body["items"]
    assert item["id"] == str(found.id)
    assert item["sender"] == "Gemeente"
    assert item["inbox_state"] == "unassigned"
    assert [p for p in item["snippet"] if p["hit"]] == [{"text": TERM, "hit": True}]


def test_a_device_token_may_search(client: Client, ws_a: Workspace) -> None:
    letter(ws_a, f"Over {TERM}.")
    headers = bearer_for(admin_of(ws_a))

    r = client.post(
        search_url(ws_a.id), {"q": TERM}, content_type="application/json", headers=headers
    )

    assert r.status_code == 200
    assert len(r.json()["items"]) == 1


def test_a_viewer_may_search(client: Client, ws_a: Workspace) -> None:
    letter(ws_a, f"Over {TERM}.")
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)

    r = client.post(search_url(ws_a.id), {"q": TERM}, content_type="application/json")

    assert r.status_code == 200
    assert len(r.json()["items"]) == 1


def test_a_letter_of_another_workspace_is_never_found(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    letter(ws_b, f"Brief van B over {TERM}.", sender=TERM)

    exact = client_a.post(search_url(ws_a.id), {"q": TERM}, content_type="application/json")
    typo = client_a.post(
        search_url(ws_a.id), {"q": TERM[:-1] + "q"}, content_type="application/json"
    )

    assert exact.json()["items"] == []
    assert typo.json()["items"] == []


def test_a_workspace_the_caller_is_not_in_is_404(client_a: Client, ws_b: Workspace) -> None:
    letter(ws_b, f"Brief van B over {TERM}.")

    r = client_a.post(search_url(ws_b.id), {"q": TERM}, content_type="application/json")

    assert r.status_code == 404
    assert r.json()["code"] == "workspace_not_found"


def test_a_term_of_201_characters_is_refused(client_a: Client, ws_a: Workspace) -> None:
    term = "a" * 201

    r = client_a.post(search_url(ws_a.id), {"q": term}, content_type="application/json")

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert "a" * 20 not in r.content.decode()


@pytest.mark.parametrize(
    "extra",
    [
        {"inbox_state": "gesorteerd"},
        {"document_type": "brief"},
        {"date_from": "2026-02-01", "date_to": "2026-01-01"},
        {"limit": 0},
        {"limit": 101},
        {"offset": 1001},
    ],
)
def test_bad_filters_are_422_without_the_term(
    client_a: Client, ws_a: Workspace, extra: dict[str, object]
) -> None:
    r = client_a.post(search_url(ws_a.id), {"q": TERM, **extra}, content_type="application/json")

    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error"
    assert body["message"]
    assert TERM not in r.content.decode()


def test_a_bsn_is_removed_and_never_echoed(client_a: Client, ws_a: Workspace) -> None:
    r = client_a.post(search_url(ws_a.id), {"q": "BSN 111222333"}, content_type="application/json")

    assert r.status_code == 200
    assert r.json()["bsn_removed"] is True
    assert "111222333" not in r.content.decode()


def test_the_term_is_in_no_log_line_and_no_audit_row(
    client_a: Client, ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    letter(ws_a, f"Brief over {TERM}.")
    with workspace_context(ws_a.id):
        audit_before = WorkspaceAuditEvent.objects.count()
    server_before = ServerAuditEvent.objects.count()

    with caplog.at_level(logging.DEBUG):
        client_a.post(search_url(ws_a.id), {"q": TERM}, content_type="application/json")
        client_a.post(search_url(ws_a.id), {"q": TERM + "q"}, content_type="application/json")
        client_a.post(
            search_url(ws_a.id),
            {"q": TERM, "inbox_state": "x"},
            content_type="application/json",
        )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert TERM not in logged
    assert "Brief over" not in logged
    with workspace_context(ws_a.id):
        assert WorkspaceAuditEvent.objects.count() == audit_before
        rows = list(WorkspaceAuditEvent.objects.values())
    server_rows = list(ServerAuditEvent.objects.values())
    assert ServerAuditEvent.objects.count() == server_before
    assert TERM not in json.dumps([rows, server_rows], default=str)


def test_the_operation_is_documented(client_a: Client) -> None:
    from apps.common.management.commands.export_openapi import render_schema

    schema = json.loads(render_schema())
    operation = schema["paths"]["/api/v1/workspaces/{workspace_id}/documents/search"]["post"]
    assert operation["operationId"] == "searchDocuments"
    hit = schema["components"]["schemas"]["DocumentSearchHitOut"]
    assert "snippet" in hit["properties"]
    assert set(schema["components"]["schemas"]["SnippetPartOut"]["properties"]) == {
        "text",
        "hit",
    }

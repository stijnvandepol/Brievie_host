import uuid
from decimal import Decimal

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.common.management.commands.export_openapi import render_schema
from apps.documents.models import Document, Extraction
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, documents_url, jpeg, upload
from tests.factories import UserFactory
from tests.helpers import post_json


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def set_status(workspace: Workspace, document_id: str, status: str, code: str = "") -> None:
    with workspace_context(workspace.id, actor=SYSTEM):
        Document.objects.filter(id=document_id).update(status=status, failure_code=code)


def test_list_is_newest_first_with_summary_fields(client_a: Client, ws_a: Workspace) -> None:
    first = upload(client_a, ws_a.id, [jpeg("een")]).json()
    second = upload(client_a, ws_a.id, [jpeg("twee"), jpeg("drie")]).json()

    r = client_a.get(documents_url(ws_a.id))

    assert r.status_code == 200
    rows = r.json()
    assert [row["id"] for row in rows] == [second["id"], first["id"]]
    assert set(rows[0]) == {
        "id", "client_id", "status", "page_count", "created_at", "sender", "document_type",
        "letter_date", "addressee_ids", "label_ids", "inbox_state", "open_suggestion_count",
        "confirmed_by_ai", "source", "has_original", "original_name",
        "auto_label_ids", "auto_addressee_ids", "auto_rule_label_ids",
        "proposed_sender",
        "proposed_document_type", "proposed_letter_date", "open_duplicate_count",
        "visibility", "reader_names", "unread",
        "letter_status", "overdue", "open_task_count", "waiting_until",
        "kind", "title", "expires_on", "remind_days", "retain_until", "reminder_task_id",
    }  # fmt: skip
    assert rows[0]["page_count"] == 2
    assert rows[0]["status"] == "processing"
    assert rows[0]["letter_date"] is None


def test_detail_shows_proposals_and_tasks(client_a: Client, ws_a: Workspace) -> None:
    document_id = upload(client_a, ws_a.id, [jpeg()], texts=["vóór 1 december 2026"]).json()["id"]
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = Document.objects.get()
        Extraction.objects.create(
            workspace=ws_a, document=document, field="action_deadline", group=1,
            value="2026-12-01", quote="vóór 1 december 2026", confidence=Decimal("0.800"),
            provider="mistral", model="mistral-small-latest",
        )  # fmt: skip
        Extraction.objects.create(
            workspace=ws_a, document=document, field="sender", value="Belastingdienst",
            quote="Belastingdienst", confidence=Decimal("0.4"),
        )  # fmt: skip
        Task.objects.create(
            workspace=ws_a, document=document, type="pay", description="Betalen",
            deadline="2026-12-01", deadline_source="in_document",
            deadline_quote="vóór 1 december 2026",
        )  # fmt: skip

    body = client_a.get(f"{documents_url(ws_a.id)}/{document_id}").json()

    assert body["proposals"] == [
        {
            "field": "sender", "group": 0, "value": "Belastingdienst", "quote": "Belastingdienst",
            "confidence": "0.400", "needs_attention": False, "provider": "", "model": "",
        },
        {
            "field": "action_deadline", "group": 1, "value": "2026-12-01",
            "quote": "vóór 1 december 2026", "confidence": "0.800", "needs_attention": True,
            "provider": "mistral", "model": "mistral-small-latest",
        },
    ]  # fmt: skip
    (task,) = body["tasks"]
    assert task["type"] == "pay"
    assert task["deadline"] == "2026-12-01"
    assert task["deadline_source"] == "in_document"
    assert task["deadline_quote"] == "vóór 1 december 2026"
    assert task["status"] == "open"
    assert task["completed_at"] is None
    assert body["failure_code"] == ""
    assert body["summary_by_ai"] is False


def test_unknown_document_is_404(client_a: Client, ws_a: Workspace) -> None:
    r = client_a.get(f"{documents_url(ws_a.id)}/{uuid.uuid4()}")

    assert r.status_code == 404
    assert r.json()["code"] == "document_not_found"


def test_viewer_may_read_but_not_retry(client: Client, client_a: Client, ws_a: Workspace) -> None:
    document_id = upload(client_a, ws_a.id, [jpeg()]).json()["id"]
    set_status(ws_a, document_id, "failed", "ai_unavailable")
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)
    base = f"{documents_url(ws_a.id)}/{document_id}"

    assert client.get(documents_url(ws_a.id)).status_code == 200
    assert client.get(base).status_code == 200
    assert client.get(f"{base}/pages/1").status_code == 200
    assert (
        post_json(client, f"/workspaces/{ws_a.id}/documents/{document_id}/retry").status_code == 403
    )


def test_retry_processing_of_a_failed_document(client_a: Client, ws_a: Workspace) -> None:
    document_id = upload(client_a, ws_a.id, [jpeg()]).json()["id"]
    set_status(ws_a, document_id, "failed", "ai_unavailable")

    r = post_json(client_a, f"/workspaces/{ws_a.id}/documents/{document_id}/retry")

    assert r.status_code == 202
    assert r.json()["status"] == "processing"
    assert r.json()["failure_code"] == ""
    assert ProcrastinateJob.objects.count() == 2


def test_retry_processing_needs_a_failed_document(client_a: Client, ws_a: Workspace) -> None:
    document_id = upload(client_a, ws_a.id, [jpeg()]).json()["id"]
    set_status(ws_a, document_id, "needs_review")

    r = post_json(client_a, f"/workspaces/{ws_a.id}/documents/{document_id}/retry")

    assert r.status_code == 409
    assert r.json()["code"] == "document_not_failed"
    assert ProcrastinateJob.objects.count() == 1


def test_retry_while_processing_says_it_is_being_processed(
    client_a: Client, ws_a: Workspace
) -> None:
    # The finding: "hoeft niet opnieuw verwerkt te worden" while it was being processed.
    document_id = upload(client_a, ws_a.id, [jpeg()]).json()["id"]

    r = post_json(client_a, f"/workspaces/{ws_a.id}/documents/{document_id}/retry")

    assert r.status_code == 409
    assert r.json() == {
        "code": "document_not_failed",
        "message": "Dit document wordt nog verwerkt. Probeer het zo opnieuw.",
    }


def test_member_of_another_workspace_gets_404_everywhere(
    client: Client, client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    # Spec acceptance: a user of workspace A sees nothing of workspace B, per endpoint.
    document_id = upload(client_a, ws_a.id, [jpeg()]).json()["id"]
    set_status(ws_a, document_id, "failed")
    client.force_login(admin_of(ws_b))
    base = f"{documents_url(ws_a.id)}/{document_id}"
    unknown = client.get(documents_url(uuid.uuid4())).json()

    responses = [
        client.get(documents_url(ws_a.id)),
        client.get(base),
        client.get(f"{base}/pages/1"),
        post_json(client, f"/workspaces/{ws_a.id}/documents/{document_id}/retry"),
        upload(client, ws_a.id, [jpeg()]),
    ]

    for r in responses:
        assert r.status_code == 404
        assert r.json() == unknown
        assert r.json()["code"] == "workspace_not_found"


def test_document_of_another_workspace_is_not_found_through_the_own_workspace(
    client: Client, client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    document_id = upload(client_a, ws_a.id, [jpeg()]).json()["id"]
    client.force_login(admin_of(ws_b))
    base = f"{documents_url(ws_b.id)}/{document_id}"

    assert client.get(documents_url(ws_b.id)).json() == []
    assert client.get(base).json()["code"] == "document_not_found"
    assert client.get(f"{base}/pages/1").status_code == 404
    assert (
        post_json(client, f"/workspaces/{ws_b.id}/documents/{document_id}/retry").status_code == 404
    )


def test_contract_describes_the_upload_metadata() -> None:
    import json

    schemas = json.loads(render_schema())["components"]["schemas"]
    metadata = schemas["UploadMetadataIn"]["properties"]
    assert metadata["client_id"]["format"] == "uuid"
    assert metadata["pages"]["items"] == {"$ref": "#/components/schemas/PageMetaIn"}
    assert set(schemas["PageMetaIn"]["properties"]) == {"sha256", "text"}


def propose(
    workspace: Workspace, document_id: str, field: str, value: str, confidence: str
) -> None:
    with workspace_context(workspace.id, actor=SYSTEM):
        Extraction.objects.create(
            workspace=workspace, document_id=document_id, field=field, value=value,
            quote=value, confidence=Decimal(confidence),
        )  # fmt: skip


def test_a_letter_waiting_for_review_shows_the_proposed_sender_date_and_type(
    client_a: Client, ws_a: Workspace
) -> None:
    # From the app test: every letter in "Te doen" read "Onbekende afzender".
    document_id = upload(client_a, ws_a.id, [jpeg()]).json()["id"]
    set_status(ws_a, document_id, "needs_review")
    propose(ws_a, document_id, "sender", "Gemeente", "0.4")
    propose(ws_a, document_id, "sender", "Belastingdienst", "0.9")
    propose(ws_a, document_id, "document_type", "assessment", "0.5")
    propose(ws_a, document_id, "letter_date", "2026-11-03", "0.9")
    # A value that was not found in the letter (confidence 0) is no proposal to show.
    other_id = upload(client_a, ws_a.id, [jpeg("ander")]).json()["id"]
    set_status(ws_a, other_id, "needs_review")
    propose(ws_a, other_id, "sender", "Verzonnen BV", "0")
    propose(ws_a, other_id, "letter_date", "2026-11-31", "0.9")

    rows = {row["id"]: row for row in client_a.get(documents_url(ws_a.id)).json()}

    shown = rows[document_id]
    assert (shown["sender"], shown["document_type"], shown["letter_date"]) == ("", "", None)
    assert (
        shown["proposed_sender"],
        shown["proposed_document_type"],
        shown["proposed_letter_date"],
    ) == ("Belastingdienst", "assessment", "2026-11-03")
    detail = client_a.get(f"{documents_url(ws_a.id)}/{document_id}").json()
    assert {(p["field"], p["value"]) for p in detail["proposals"]} >= {
        ("sender", "Belastingdienst"),
        ("document_type", "assessment"),
        ("letter_date", "2026-11-03"),
    }
    assert detail["proposed_sender"] == "Belastingdienst"
    other = rows[other_id]
    assert (other["proposed_sender"], other["proposed_letter_date"]) == ("", None)


def test_a_confirmed_letter_shows_no_proposal(client_a: Client, ws_a: Workspace) -> None:
    document_id = upload(client_a, ws_a.id, [jpeg()]).json()["id"]
    propose(ws_a, document_id, "sender", "Belastingdienst", "0.9")
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document_id).update(
            status="done", confirmed_at=timezone.now(), sender=""
        )

    (row,) = client_a.get(documents_url(ws_a.id)).json()

    assert (row["sender"], row["proposed_sender"]) == ("", "")


def test_the_list_reads_the_proposals_in_a_fixed_number_of_queries(
    client_a: Client, ws_a: Workspace
) -> None:
    def add(n: int) -> None:
        for i in range(n):
            document_id = upload(client_a, ws_a.id, [jpeg(f"{n}-{i}")]).json()["id"]
            set_status(ws_a, document_id, "needs_review")
            propose(ws_a, document_id, "sender", f"Afzender {n}-{i}", "0.9")

    add(2)
    with CaptureQueriesContext(connection) as few:
        client_a.get(documents_url(ws_a.id))
    add(5)
    with CaptureQueriesContext(connection) as more:
        rows = client_a.get(documents_url(ws_a.id)).json()

    assert len(rows) == 7
    assert all(row["proposed_sender"] for row in rows)
    assert len(more.captured_queries) == len(few.captured_queries)

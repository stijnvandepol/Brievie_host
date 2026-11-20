import uuid
from typing import TYPE_CHECKING

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.documents import organise
from apps.documents.models import Document, DocumentEvent, DocumentLabel
from apps.labels.models import Label
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, documents_url, make_document
from tests.helpers import post_json

if TYPE_CHECKING:
    from tests.helpers import Response


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def label(workspace: Workspace, key: str) -> Label:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Label.objects.get(workspace=workspace, system_key=key)


def put_labels(
    client: Client, workspace_id: object, document_id: object, label_ids: list[object]
) -> "Response":
    return client.put(
        f"{documents_url(workspace_id)}/{document_id}/labels",
        {"label_ids": [str(i) for i in label_ids]},
        content_type="application/json",
    )


def events(workspace: Workspace, document: Document) -> list[tuple[str, str, uuid.UUID | None]]:
    """(kind, label_name, label_id) of the document's events, oldest first."""
    with workspace_context(workspace.id, actor=SYSTEM):
        return [
            (e.kind, e.label_name, e.label_id)
            for e in DocumentEvent.objects.filter(document=document).order_by("created_at", "id")
        ]


def test_set_labels_exact_set_and_events(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a)
    zorg, bank, auto = (label(ws_a, key) for key in ("zorg", "bank", "auto"))

    r = put_labels(client_a, ws_a.id, document.id, [zorg.id, bank.id])
    assert r.status_code == 200
    assert set(r.json()["label_ids"]) == {str(zorg.id), str(bank.id)}

    r = put_labels(client_a, ws_a.id, document.id, [bank.id, auto.id, auto.id])
    assert r.status_code == 200
    assert set(r.json()["label_ids"]) == {str(bank.id), str(auto.id)}
    # The same set again changes nothing.
    assert put_labels(client_a, ws_a.id, document.id, [auto.id, bank.id]).status_code == 200

    assert events(ws_a, document) == [
        ("label_added", "Zorg", zorg.id),
        ("label_added", "Bank", bank.id),
        ("label_removed", "Zorg", zorg.id),
        ("label_added", "Auto", auto.id),
    ]
    with workspace_context(ws_a.id, actor=SYSTEM):
        event = DocumentEvent.objects.filter(document=document).first()
        assert event is not None
        assert (event.origin, event.actor) == ("user", admin_of(ws_a))


def test_add_label_reports_whether_it_changed_anything(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    zorg = label(ws_a, "zorg")

    with workspace_context(ws_a.id, actor=SYSTEM):
        first = organise.add_label(document, zorg, actor=None, origin="rule")
        second = organise.add_label(document, zorg, actor=None, origin="rule")

    assert (first, second) == (True, False)
    assert events(ws_a, document) == [("label_added", "Zorg", zorg.id)]


def test_label_of_another_workspace_is_404(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    document = make_document(ws_a)
    zorg = label(ws_a, "zorg")
    put_labels(client_a, ws_a.id, document.id, [zorg.id])

    r = put_labels(client_a, ws_a.id, document.id, [zorg.id, label(ws_b, "bank").id])

    assert r.status_code == 404
    assert r.json()["code"] == "label_not_found"
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert list(DocumentLabel.objects.values_list("label_id", flat=True)) == [zorg.id]
    assert len(events(ws_a, document)) == 1


def test_labels_of_a_processing_document_are_409(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "processing")

    r = put_labels(client_a, ws_a.id, document.id, [label(ws_a, "zorg").id])

    assert r.status_code == 409
    assert r.json()["code"] == "document_processing"


def test_more_than_thirty_labels_is_422(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a)

    r = put_labels(client_a, ws_a.id, document.id, [uuid.uuid4() for _ in range(31)])

    assert r.status_code == 422


def test_delete_label_removes_it_from_documents_and_keeps_event_names(
    client_a: Client, ws_a: Workspace
) -> None:
    document = make_document(ws_a)
    zorg, bank = label(ws_a, "zorg"), label(ws_a, "bank")
    put_labels(client_a, ws_a.id, document.id, [zorg.id, bank.id])
    rows = client_a.get(f"/api/v1/workspaces/{ws_a.id}/labels").json()
    assert next(row for row in rows if row["id"] == str(zorg.id))["document_count"] == 1

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/labels/{zorg.id}")

    assert r.status_code == 204
    body = client_a.get(f"{documents_url(ws_a.id)}/{document.id}").json()
    assert body["label_ids"] == [str(bank.id)]
    # A removed line per document for the delete; the events keep the name.
    assert events(ws_a, document) == [
        ("label_added", "Zorg", None),
        ("label_added", "Bank", bank.id),
        ("label_removed", "Zorg", None),
    ]


def test_list_filter_on_label(client_a: Client, ws_a: Workspace) -> None:
    zorg, bank = label(ws_a, "zorg"), label(ws_a, "bank")
    with_zorg = make_document(ws_a)
    with_both = make_document(ws_a)
    make_document(ws_a)
    put_labels(client_a, ws_a.id, with_zorg.id, [zorg.id])
    put_labels(client_a, ws_a.id, with_both.id, [zorg.id, bank.id])

    by_zorg = client_a.get(documents_url(ws_a.id), {"label_id": str(zorg.id)}).json()
    by_bank = client_a.get(documents_url(ws_a.id), {"label_id": str(bank.id)}).json()

    assert {row["id"] for row in by_zorg} == {str(with_zorg.id), str(with_both.id)}
    assert [row["id"] for row in by_bank] == [str(with_both.id)]
    assert set(by_bank[0]["label_ids"]) == {str(zorg.id), str(bank.id)}


def test_deleting_a_label_writes_its_removed_lines_in_one_insert(
    client_a: Client, ws_a: Workspace
) -> None:
    def delete_on(count: int) -> int:
        created = post_json(
            client_a, f"/workspaces/{ws_a.id}/labels", {"name": f"Tijdelijk {count}"}
        )
        label_id = created.json()["id"]
        for _ in range(count):
            put_labels(client_a, ws_a.id, make_document(ws_a).id, [uuid.UUID(label_id)])
        with CaptureQueriesContext(connection) as queries:
            r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/labels/{label_id}")
        assert r.status_code == 204
        with workspace_context(ws_a.id, actor=SYSTEM):
            assert (
                DocumentEvent.objects.filter(
                    label_name=f"Tijdelijk {count}", kind="label_removed"
                ).count()
                == count
            )
        return len(queries.captured_queries)

    assert delete_on(1) == delete_on(6)

import json
import uuid
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.common.management.commands.export_openapi import render_schema
from apps.documents.models import DocumentAddressee, DocumentEvent
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, documents_url, make_document, make_person
from tests.factories import UserFactory

if TYPE_CHECKING:
    from tests.helpers import Response


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def put_addressees(
    client: Client, workspace_id: object, document_id: object, person_ids: list[object]
) -> "Response":
    return client.put(
        f"{documents_url(workspace_id)}/{document_id}/addressees",
        {"person_ids": [str(i) for i in person_ids]},
        content_type="application/json",
    )


def counts_url(workspace_id: object) -> str:
    return f"/api/v1/workspaces/{workspace_id}/inbox/counts"


def test_set_addressees_returns_the_document(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    papa = make_person(ws_a, "Papa")

    r = put_addressees(client_a, ws_a.id, document.id, [papa.id])

    assert r.status_code == 200
    body = r.json()
    assert body["addressee_ids"] == [str(papa.id)]
    assert body["label_ids"] == []
    assert body["inbox_state"] == ""
    assert body["open_suggestion_count"] == 0


def test_more_than_ten_addressees_is_422(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")

    r = put_addressees(client_a, ws_a.id, document.id, [uuid.uuid4() for _ in range(11)])

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_unknown_person_is_404(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")

    r = put_addressees(client_a, ws_a.id, document.id, [uuid.uuid4()])

    assert r.status_code == 404
    assert r.json()["code"] == "person_not_found"


def test_processing_document_is_409(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "processing")
    papa = make_person(ws_a, "Papa")

    r = put_addressees(client_a, ws_a.id, document.id, [papa.id])

    assert r.status_code == 409
    assert r.json()["code"] == "document_processing"
    with workspace_context(ws_a.id):
        assert not DocumentAddressee.objects.exists()


def test_viewer_cannot_set_addressees(client: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    papa = make_person(ws_a, "Papa")
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)

    r = put_addressees(client, ws_a.id, document.id, [papa.id])

    assert r.status_code == 403
    with workspace_context(ws_a.id):
        assert not DocumentEvent.objects.exists()


def test_list_filters(client_a: Client, ws_a: Workspace) -> None:
    papa = make_person(ws_a, "Papa")
    unassigned = make_document(ws_a, "done")
    for_papa = make_document(ws_a, "done")
    make_document(ws_a, "processing")
    put_addressees(client_a, ws_a.id, for_papa.id, [papa.id])

    by_state = client_a.get(documents_url(ws_a.id), {"inbox_state": "unassigned"})
    by_person = client_a.get(documents_url(ws_a.id), {"person_id": str(papa.id)})

    assert by_state.status_code == 200
    assert [row["id"] for row in by_state.json()] == [str(unassigned.id)]
    assert by_state.json()[0]["inbox_state"] == "unassigned"
    assert [row["id"] for row in by_person.json()] == [str(for_papa.id)]
    assert by_person.json()[0]["addressee_ids"] == [str(papa.id)]


def test_label_filter_with_an_unknown_label_finds_nothing(
    client_a: Client, ws_a: Workspace
) -> None:
    make_document(ws_a, "done")

    r = client_a.get(documents_url(ws_a.id), {"label_id": str(uuid.uuid4())})

    assert r.status_code == 200
    assert r.json() == []


def test_inbox_counts(client_a: Client, ws_a: Workspace) -> None:
    papa = make_person(ws_a, "Papa")
    make_document(ws_a, "processing")
    make_document(ws_a, "needs_review")
    make_document(ws_a, "failed")
    make_document(ws_a, "done")
    put_addressees(client_a, ws_a.id, make_document(ws_a, "done").id, [papa.id])

    r = client_a.get(counts_url(ws_a.id))

    assert r.status_code == 200
    assert r.json() == {"new": 1, "review": 2, "unassigned": 1}


@pytest.mark.parametrize("query", [{"inbox_state": "done"}, {"person_id": "geen-uuid"}])
def test_invalid_filter_is_422(client_a: Client, ws_a: Workspace, query: dict[str, str]) -> None:
    r = client_a.get(documents_url(ws_a.id), query)

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_exported_query_parameters_have_no_anyof() -> None:
    schema = json.loads(render_schema())
    operation = schema["paths"]["/api/v1/workspaces/{workspace_id}/documents"]["get"]
    parameters = {p["name"]: p for p in operation["parameters"] if p["in"] == "query"}

    assert set(parameters) == {"inbox_state", "person_id", "label_id"}
    assert not any(p["required"] for p in parameters.values())
    assert "anyOf" not in json.dumps(parameters)
    assert parameters["person_id"]["schema"]["format"] == "uuid"


def test_other_workspace_is_404_on_every_new_endpoint(
    client: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    document = make_document(ws_a, "done")
    papa = make_person(ws_a, "Papa")
    client.force_login(admin_of(ws_b))
    base = f"{documents_url(ws_a.id)}/{document.id}"

    through_a = [
        put_addressees(client, ws_a.id, document.id, [papa.id]),
        client.get(counts_url(ws_a.id)),
        client.get(f"{base}/events"),
        client.get(documents_url(ws_a.id), {"person_id": str(papa.id)}),
    ]
    for r in through_a:
        assert r.status_code == 404
        assert r.json()["code"] == "workspace_not_found"

    # Through the own workspace the ids of another workspace do not exist.
    own = put_addressees(client, ws_b.id, document.id, [])
    assert own.json()["code"] == "document_not_found"
    assert client.get(f"{documents_url(ws_b.id)}/{document.id}/events").status_code == 404
    assert client.get(documents_url(ws_b.id), {"person_id": str(papa.id)}).json() == []
    assert client.get(counts_url(ws_b.id)).json() == {"new": 0, "review": 0, "unassigned": 0}

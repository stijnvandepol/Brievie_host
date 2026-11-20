from django.test import Client

from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, documents_url, make_document, make_person
from tests.factories import UserFactory
from tests.helpers import post_json


def put_addressees(
    client: Client, workspace: Workspace, document_id: object, ids: list[str]
) -> int:
    r = client.put(
        f"{documents_url(workspace.id)}/{document_id}/addressees",
        {"person_ids": ids},
        content_type="application/json",
    )
    return r.status_code


def test_events_newest_first_with_names(client: Client, ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    client.force_login(admin)
    document = make_document(ws_a, "needs_review")
    papa = make_person(ws_a, "Papa")
    base = f"{documents_url(ws_a.id)}/{document.id}"

    assert put_addressees(client, ws_a, document.id, [str(papa.id)]) == 200
    confirmed = post_json(
        client,
        f"/workspaces/{ws_a.id}/documents/{document.id}/confirm",
        {
            "sender": "",
            "document_type": "",
            "summary": "",
            "summary_by_ai": False,
            "tasks": [],
        },
    )
    assert confirmed.status_code == 200
    assert put_addressees(client, ws_a, document.id, []) == 200
    # The person is no longer on any document, so it can go; the history keeps the name.
    deleted = client.delete(f"/api/v1/workspaces/{ws_a.id}/persons/{papa.id}")
    assert deleted.status_code == 204

    r = client.get(f"{base}/events")

    assert r.status_code == 200
    rows = r.json()
    assert [(row["kind"], row["person_name"]) for row in rows] == [
        ("addressee_removed", "Papa"),
        ("confirmed", ""),
        ("addressee_added", "Papa"),
    ]
    assert set(rows[0]) == {
        "id", "created_at", "kind", "origin", "actor_id", "actor_name", "rule_name",
        "label_name", "person_name", "can_undo", "undone",
    }  # fmt: skip
    for row in rows:
        assert row["origin"] == "user"
        assert row["actor_id"] == str(admin.id)
        assert row["actor_name"] == admin.name
        assert row["rule_name"] == ""
        assert row["label_name"] == ""
        assert row["can_undo"] is False
        assert row["undone"] is False


def test_person_on_a_document_cannot_be_deleted(client: Client, ws_a: Workspace) -> None:
    client.force_login(admin_of(ws_a))
    document = make_document(ws_a, "done")
    papa = make_person(ws_a, "Papa")
    assert put_addressees(client, ws_a, document.id, [str(papa.id)]) == 200

    r = client.delete(f"/api/v1/workspaces/{ws_a.id}/persons/{papa.id}")
    persons = client.get(f"/api/v1/workspaces/{ws_a.id}/persons").json()

    assert r.status_code == 409
    assert r.json()["code"] == "person_in_use"
    assert {p["display_name"]: p["document_count"] for p in persons}["Papa"] == 1


def test_viewer_may_read_the_history(client: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)

    r = client.get(f"{documents_url(ws_a.id)}/{document.id}/events")

    assert r.status_code == 200
    assert r.json() == []


def test_deleting_a_label_leaves_a_removed_line_in_each_letters_history(
    client: Client, ws_a: Workspace
) -> None:
    # The finding: the history still ended in `label_added` after the label was deleted.
    admin = admin_of(ws_a)
    client.force_login(admin)
    first, second = make_document(ws_a, "needs_review"), make_document(ws_a, "needs_review")
    created = post_json(client, f"/workspaces/{ws_a.id}/labels", {"name": "TEST tijdelijk"})
    label_id = created.json()["id"]
    for document in (first, second):
        r = client.put(
            f"{documents_url(ws_a.id)}/{document.id}/labels",
            {"label_ids": [label_id]},
            content_type="application/json",
        )
        assert r.status_code == 200

    assert client.delete(f"/api/v1/workspaces/{ws_a.id}/labels/{label_id}").status_code == 204

    for document in (first, second):
        rows = client.get(f"{documents_url(ws_a.id)}/{document.id}/events").json()
        assert [(row["kind"], row["label_name"]) for row in rows] == [
            ("label_removed", "TEST tijdelijk"),
            ("label_added", "TEST tijdelijk"),
        ]
        assert (rows[0]["origin"], rows[0]["actor_id"]) == ("user", str(admin.id))
        assert rows[0]["can_undo"] is False
        assert client.get(f"{documents_url(ws_a.id)}/{document.id}").json()["label_ids"] == []

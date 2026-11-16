import uuid
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.documents.models import Document, DocumentEvent, DocumentLabel, DocumentSuggestion
from apps.labels.models import Label
from apps.pipeline.steps import run_pipeline
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, make_person
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.rules_support import add_addressee, add_label, make_document, make_rule, text_word

if TYPE_CHECKING:
    from tests.helpers import Response

LETTER = "Uitnodiging voor de APK van uw auto."


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def auto_label(workspace: Workspace) -> Label:
    with workspace_context(workspace.id):
        return Label.objects.get(workspace=workspace, system_key="auto")


def processed_with_auto_rule(workspace: Workspace) -> tuple[Document, Label]:
    """A document whose label "Auto" was put on by an automatic rule."""
    label = auto_label(workspace)
    make_rule(workspace, text_word("APK"), actions=[add_label(label)], name="APK", mode="auto")
    document = make_document(workspace, LETTER, status="processing")
    run_pipeline(workspace.id, document.id, provider_factory=lambda _: None)
    return document, label


def events(client: Client, document: Document) -> list[dict[str, object]]:
    r = client.get(f"/api/v1/workspaces/{document.workspace_id}/documents/{document.id}/events")
    assert r.status_code == 200
    rows: list[dict[str, object]] = r.json()
    return rows


def undo(client: Client, document: Document, event_id: object) -> "Response":
    return post_json(
        client,
        f"/workspaces/{document.workspace_id}/documents/{document.id}/events/{event_id}/undo",
    )


def rule_event_id(document: Document) -> uuid.UUID:
    with workspace_context(document.workspace_id):
        return DocumentEvent.objects.get(document=document, origin="rule").id


def test_undo_removes_the_label_and_blocks_the_rule(client_a: Client, ws_a: Workspace) -> None:
    document, label = processed_with_auto_rule(ws_a)
    [added] = events(client_a, document)
    assert (added["kind"], added["origin"], added["rule_name"], added["label_name"]) == (
        "label_added",
        "rule",
        "APK",
        "Auto",
    )
    assert (added["can_undo"], added["undone"]) == (True, False)

    r = undo(client_a, document, added["id"])

    assert r.status_code == 200
    assert str(label.id) not in r.json()["label_ids"]
    reversed_, original = events(client_a, document)
    assert (reversed_["kind"], reversed_["origin"], reversed_["label_name"]) == (
        "label_removed",
        "undo",
        "Auto",
    )
    assert reversed_["can_undo"] is False
    assert (original["can_undo"], original["undone"]) == (False, True)
    with workspace_context(ws_a.id):
        undo_event = DocumentEvent.objects.get(origin="undo")
        assert str(undo_event.undoes_id) == original["id"]
        assert DocumentSuggestion.objects.get(document=document).state == "dismissed"

    # Confirming runs the rules again: the rule does not put the label back.
    confirmed = post_json(
        client_a,
        f"/workspaces/{ws_a.id}/documents/{document.id}/confirm",
        {"sender": "", "document_type": "", "summary": "", "summary_by_ai": False, "tasks": []},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["label_ids"] == []


def test_undo_of_an_addressee_added_by_a_rule(client_a: Client, ws_a: Workspace) -> None:
    emma = make_person(ws_a, "Emma")
    make_rule(ws_a, text_word("APK"), actions=[add_addressee(emma.id)], mode="auto")
    document = make_document(ws_a, LETTER, status="processing")
    run_pipeline(ws_a.id, document.id, provider_factory=lambda _: None)

    r = undo(client_a, document, rule_event_id(document))

    assert r.status_code == 200
    assert r.json()["addressee_ids"] == []
    assert events(client_a, document)[0]["kind"] == "addressee_removed"


def test_undo_twice_is_409(client_a: Client, ws_a: Workspace) -> None:
    document, _ = processed_with_auto_rule(ws_a)
    event_id = rule_event_id(document)

    assert undo(client_a, document, event_id).status_code == 200
    again = undo(client_a, document, event_id)

    assert again.status_code == 409
    assert again.json()["code"] == "event_not_undoable"


def test_undo_after_the_label_was_removed_by_hand_is_409(client_a: Client, ws_a: Workspace) -> None:
    document, _ = processed_with_auto_rule(ws_a)
    event_id = rule_event_id(document)
    removed = client_a.put(
        f"/api/v1/workspaces/{ws_a.id}/documents/{document.id}/labels",
        {"label_ids": []},
        content_type="application/json",
    )
    assert removed.status_code == 200
    assert [row["can_undo"] for row in events(client_a, document)] == [False, False]

    r = undo(client_a, document, event_id)

    assert r.status_code == 409
    assert r.json()["code"] == "event_not_undoable"


def test_undo_of_a_hand_event_is_409(client_a: Client, ws_a: Workspace) -> None:
    label = auto_label(ws_a)
    document = make_document(ws_a, LETTER)
    added = client_a.put(
        f"/api/v1/workspaces/{ws_a.id}/documents/{document.id}/labels",
        {"label_ids": [str(label.id)]},
        content_type="application/json",
    )
    assert added.status_code == 200
    [event] = events(client_a, document)
    assert event["can_undo"] is False

    r = undo(client_a, document, event["id"])

    assert r.status_code == 409
    assert r.json()["code"] == "event_not_undoable"
    with workspace_context(ws_a.id):
        assert DocumentLabel.objects.filter(document=document).exists()


def test_unknown_event_is_404(client_a: Client, ws_a: Workspace) -> None:
    document, _ = processed_with_auto_rule(ws_a)
    other, _ = processed_with_auto_rule(ws_a)

    for event_id in (uuid.uuid4(), rule_event_id(other)):
        r = undo(client_a, document, event_id)
        assert r.status_code == 404
        assert r.json()["code"] == "event_not_found"


def test_viewer_cannot_undo(client: Client, ws_a: Workspace) -> None:
    document, _ = processed_with_auto_rule(ws_a)
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)

    r = undo(client, document, rule_event_id(document))

    assert r.status_code == 403
    with workspace_context(ws_a.id):
        assert DocumentLabel.objects.filter(document=document).exists()


def test_member_of_another_workspace_gets_404(
    client: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    document, _ = processed_with_auto_rule(ws_a)
    client.force_login(admin_of(ws_b))

    r = undo(client, document, rule_event_id(document))

    assert r.status_code == 404
    assert r.json()["code"] == "workspace_not_found"

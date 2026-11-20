"""What clients see of the autopilot: summary fields, the `automatic` filter, the counter."""

import uuid
from typing import TYPE_CHECKING

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.documents import visibility
from apps.documents.models import Document, DocumentAddressee, DocumentLabel
from apps.labels.models import Label
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, documents_url, make_document, make_person
from tests.helpers import post_json
from tests.test_autopilot_confirm import Answering, switch_on
from tests.test_autopilot_confirm import new_document as new_processing_document
from tests.test_autopilot_confirm import run as run_pipeline_for

if TYPE_CHECKING:
    from apps.people.models import Person

pytestmark = pytest.mark.usefixtures("without_preset_rules")


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def base(workspace: Workspace, document: Document) -> str:
    return f"{documents_url(workspace.id)}/{document.id}"


def link_label(workspace: Workspace, document: Document, name: str, *, automatic: bool) -> Label:
    with workspace_context(workspace.id, actor=SYSTEM):
        label = Label.objects.get_or_create(workspace=workspace, name=name)[0]
        DocumentLabel.objects.create(
            workspace=workspace, document=document, label=label, automatic=automatic
        )
        return label


def link_person(
    workspace: Workspace, document: Document, person: "Person", *, automatic: bool
) -> None:
    with workspace_context(workspace.id, actor=SYSTEM):
        DocumentAddressee.objects.create(
            workspace=workspace, document=document, person=person, automatic=automatic
        )
        visibility.sync_addressee_ids(document)


def ids_of(client: Client, workspace: Workspace, **query: str) -> set[str]:
    r = client.get(documents_url(workspace.id), query)
    assert r.status_code == 200
    return {row["id"] for row in r.json()}


_persons = iter(range(1000))


def automatic_documents(workspace: Workspace) -> dict[str, Document]:
    """One document per way of being automatic, plus two that are not."""
    jan = make_person(workspace, f"Jan {next(_persons)}")
    ai_confirmed = make_document(workspace, "done")
    with workspace_context(workspace.id, actor=SYSTEM):
        Document.objects.filter(id=ai_confirmed.id).update(confirmed_by_ai=True)
    auto_label = make_document(workspace, "needs_review")
    link_label(workspace, auto_label, f"Automatisch {next(_persons)}", automatic=True)
    auto_person = make_document(workspace, "done")
    link_person(workspace, auto_person, jan, automatic=True)
    by_hand = make_document(workspace, "done")
    link_label(workspace, by_hand, f"Eigen {next(_persons)}", automatic=False)
    link_person(workspace, by_hand, jan, automatic=False)
    plain = make_document(workspace, "needs_review")
    return {
        "ai_confirmed": ai_confirmed,
        "auto_label": auto_label,
        "auto_person": auto_person,
        "by_hand": by_hand,
        "plain": plain,
    }


def test_a_summary_without_autopilot_has_the_defaults(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")

    [row] = client_a.get(documents_url(ws_a.id)).json()

    assert row["id"] == str(document.id)
    assert (row["confirmed_by_ai"], row["auto_label_ids"], row["auto_addressee_ids"]) == (
        False,
        [],
        [],
    )


def test_only_automatic_links_show_as_automatic(client_a: Client, ws_a: Workspace) -> None:
    jan = make_person(ws_a, "Jan")
    piet = make_person(ws_a, "Piet")
    document = make_document(ws_a, "done")
    auto = link_label(ws_a, document, "Automatisch", automatic=True)
    hand = link_label(ws_a, document, "Eigen", automatic=False)
    link_person(ws_a, document, jan, automatic=True)
    link_person(ws_a, document, piet, automatic=False)

    [row] = client_a.get(documents_url(ws_a.id)).json()
    detail = client_a.get(base(ws_a, document)).json()

    for body in (row, detail):
        assert set(body["label_ids"]) == {str(auto.id), str(hand.id)}
        assert body["auto_label_ids"] == [str(auto.id)]
        assert set(body["addressee_ids"]) == {str(jan.id), str(piet.id)}
        assert body["auto_addressee_ids"] == [str(jan.id)]


def test_fields_after_an_automatic_run_after_undo_and_after_reopen(
    client_a: Client, ws_a: Workspace
) -> None:
    switch_on(ws_a, organise=True, confirm=True)
    jan = make_person(ws_a, "Jan de Vries")
    with workspace_context(ws_a.id, actor=SYSTEM):
        label = Label.objects.create(workspace=ws_a, name="Gezondheid")
    document = new_processing_document(ws_a)

    run_pipeline_for(ws_a, document, Answering(labels={"Gezondheid": 0.9}))

    detail = client_a.get(base(ws_a, document)).json()
    assert detail["status"] == "done"
    assert detail["confirmed_by_ai"] is True
    assert detail["auto_label_ids"] == [str(label.id)]
    assert detail["auto_addressee_ids"] == [str(jan.id)]
    assert [task["created_by_ai"] for task in detail["tasks"]] == [True]
    assert ids_of(client_a, ws_a, automatic="true") == {str(document.id)}

    events = client_a.get(f"{base(ws_a, document)}/events").json()
    label_event = next(e for e in events if e["kind"] == "label_added")
    assert (label_event["origin"], label_event["can_undo"]) == ("ai", True)
    undone = post_json(
        client_a, f"/workspaces/{ws_a.id}/documents/{document.id}/events/{label_event['id']}/undo"
    )
    assert undone.status_code == 200
    assert undone.json()["auto_label_ids"] == []
    assert undone.json()["label_ids"] == []
    assert undone.json()["auto_addressee_ids"] == [str(jan.id)]

    reopened = post_json(client_a, f"/workspaces/{ws_a.id}/documents/{document.id}/reopen")
    assert reopened.status_code == 200
    body = reopened.json()
    assert body["confirmed_by_ai"] is False
    assert body["tasks"] == []
    # The addressee the autopilot added stays, and still says so.
    assert body["auto_addressee_ids"] == [str(jan.id)]
    assert ids_of(client_a, ws_a, automatic="true") == {str(document.id)}


def test_a_task_a_person_made_is_not_by_ai(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    with workspace_context(ws_a.id, actor=SYSTEM):
        Task.objects.create(workspace=ws_a, document=document, type="pay", description="Eigen")

    detail = client_a.get(base(ws_a, document)).json()

    assert [task["created_by_ai"] for task in detail["tasks"]] == [False]


def test_the_automatic_filter_alone(client_a: Client, ws_a: Workspace) -> None:
    docs = automatic_documents(ws_a)

    found = ids_of(client_a, ws_a, automatic="true")

    assert found == {str(docs[k].id) for k in ("ai_confirmed", "auto_label", "auto_person")}
    assert ids_of(client_a, ws_a, automatic="false") == {str(d.id) for d in docs.values()}
    assert ids_of(client_a, ws_a) == {str(d.id) for d in docs.values()}


def test_a_document_with_several_automatic_links_is_listed_once(
    client_a: Client, ws_a: Workspace
) -> None:
    document = make_document(ws_a, "done")
    link_label(ws_a, document, "Een", automatic=True)
    link_label(ws_a, document, "Twee", automatic=True)
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(confirmed_by_ai=True)

    rows = client_a.get(documents_url(ws_a.id), {"automatic": "true"}).json()

    assert [row["id"] for row in rows] == [str(document.id)]


def test_the_automatic_filter_combines_with_the_other_filters(
    client_a: Client, ws_a: Workspace
) -> None:
    docs = automatic_documents(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        jan = DocumentAddressee.objects.get(document=docs["auto_person"]).person
        auto_label_id = DocumentLabel.objects.get(document=docs["auto_label"]).label_id

    assert ids_of(client_a, ws_a, automatic="true", person_id=str(jan.id)) == {
        str(docs["auto_person"].id)
    }
    assert ids_of(client_a, ws_a, person_id=str(jan.id)) == {
        str(docs["auto_person"].id),
        str(docs["by_hand"].id),
    }
    assert ids_of(client_a, ws_a, automatic="true", label_id=str(auto_label_id)) == {
        str(docs["auto_label"].id)
    }
    assert ids_of(client_a, ws_a, automatic="true", inbox_state="review") == {
        str(docs["auto_label"].id)
    }
    assert ids_of(client_a, ws_a, automatic="true", label_id=str(uuid.uuid4())) == set()


def test_the_inbox_counter_counts_automatic_documents(client_a: Client, ws_a: Workspace) -> None:
    assert client_a.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts").json()["automatic"] == 0
    automatic_documents(ws_a)

    counts = client_a.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts").json()

    assert counts == {"new": 0, "review": 2, "unassigned": 1, "automatic": 3}


def test_the_list_does_not_query_per_document(client_a: Client, ws_a: Workspace) -> None:
    def queries() -> int:
        with CaptureQueriesContext(connection) as captured:
            assert client_a.get(documents_url(ws_a.id)).status_code == 200
        return len(captured)

    automatic_documents(ws_a)
    few = queries()
    automatic_documents(ws_a)
    automatic_documents(ws_a)

    assert queries() == few


def test_automatic_documents_of_another_workspace_are_not_listed_or_counted(
    client: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    from apps.documents import services

    mine = automatic_documents(ws_a)
    theirs = automatic_documents(ws_b)
    counts = "/api/v1/workspaces/{}/inbox/counts"

    client.force_login(admin_of(ws_a))
    assert ids_of(client, ws_a, automatic="true") == {
        str(mine[k].id) for k in ("ai_confirmed", "auto_label", "auto_person")
    }
    assert client.get(counts.format(ws_a.id)).json()["automatic"] == 3
    # Even a query that forgets the workspace sees only A's rows (forced RLS).
    with workspace_context(ws_a.id, actor=SYSTEM):
        unfiltered = set(
            Document.objects.filter(services._is_automatic()).values_list("id", flat=True)
        )
    assert unfiltered == {mine[k].id for k in ("ai_confirmed", "auto_label", "auto_person")}

    # Not vacuous: B's member does see B's automatic documents.
    client.force_login(admin_of(ws_b))
    assert ids_of(client, ws_b, automatic="true") == {
        str(theirs[k].id) for k in ("ai_confirmed", "auto_label", "auto_person")
    }
    assert client.get(counts.format(ws_b.id)).json()["automatic"] == 3

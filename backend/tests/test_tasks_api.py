import datetime
import uuid
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.documents.models import Document, DocumentEvent
from apps.tasks.models import Task
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.rules_support import make_document

if TYPE_CHECKING:
    from tests.helpers import Response


def tasks_url(workspace_id: object) -> str:
    return f"/api/v1/workspaces/{workspace_id}/tasks"


def make_task(
    workspace: Workspace,
    description: str = "Betaal de rekening",
    *,
    deadline: datetime.date | None = None,
    document: Document | None = None,
    status: str = "open",
) -> Task:
    with workspace_context(workspace.id):
        return Task.objects.create(
            workspace=workspace,
            document=document,
            type="pay",
            description=description,
            deadline=deadline,
            status=status,
        )


def tick(client: Client, workspace_id: object, task: Task, action: str = "complete") -> "Response":
    return post_json(client, f"/workspaces/{workspace_id}/tasks/{task.id}/{action}")


def events(workspace: Workspace) -> list[DocumentEvent]:
    with workspace_context(workspace.id):
        return list(DocumentEvent.objects.order_by("created_at", "id"))


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def test_list_open_tasks_by_deadline_nulls_last(client_a: Client, ws_a: Workspace) -> None:
    later = make_task(ws_a, "Later", deadline=datetime.date(2026, 12, 20))
    undated = make_task(ws_a, "Zonder datum")
    sooner = make_task(ws_a, "Eerst", deadline=datetime.date(2026, 12, 1))
    make_task(ws_a, "Klaar", deadline=datetime.date(2026, 11, 1), status="done")

    r = client_a.get(tasks_url(ws_a.id))

    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == [str(sooner.id), str(later.id), str(undated.id)]
    assert set(r.json()[0]) == {
        "id",
        "type",
        "description",
        "deadline",
        "deadline_source",
        "status",
        "completed_at",
        "document_id",
        "sender",
        "updated_at",
        "created_by_ai",
    }


def test_status_filter_and_invalid_status(client_a: Client, ws_a: Workspace) -> None:
    make_task(ws_a, "Open")
    done = make_task(ws_a, "Klaar", status="done")

    only_done = client_a.get(tasks_url(ws_a.id), {"status": "done"})
    everything = client_a.get(tasks_url(ws_a.id), {"status": "all"})
    invalid = client_a.get(tasks_url(ws_a.id), {"status": "soms"})

    assert [row["id"] for row in only_done.json()] == [str(done.id)]
    assert len(everything.json()) == 2
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"


def test_complete_sets_status_and_time_and_writes_event(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "Brief")
    task = make_task(ws_a, document=document)

    r = tick(client_a, ws_a.id, task)

    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["completed_at"] is not None
    (event,) = events(ws_a)
    assert (event.kind, event.origin, event.document_id) == ("task_completed", "user", document.id)
    assert event.actor_id == admin_of(ws_a).id


def test_complete_twice_is_200_and_one_event(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "Brief")
    task = make_task(ws_a, document=document)

    first = tick(client_a, ws_a.id, task)
    second = tick(client_a, ws_a.id, task)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["completed_at"] == first.json()["completed_at"]
    assert len(events(ws_a)) == 1


def test_reopen_clears_completed_at(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "Brief")
    task = make_task(ws_a, document=document)
    tick(client_a, ws_a.id, task)

    r = tick(client_a, ws_a.id, task, "reopen")
    again = tick(client_a, ws_a.id, task, "reopen")

    assert r.status_code == 200
    assert (r.json()["status"], r.json()["completed_at"]) == ("open", None)
    assert again.status_code == 200
    assert [e.kind for e in events(ws_a)] == ["task_completed", "task_reopened"]


def test_task_without_document_writes_no_event(client_a: Client, ws_a: Workspace) -> None:
    task = make_task(ws_a)

    r = tick(client_a, ws_a.id, task)

    assert r.status_code == 200
    assert events(ws_a) == []


def test_viewer_cannot_complete(client: Client, ws_a: Workspace) -> None:
    task = make_task(ws_a)
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)

    assert tick(client, ws_a.id, task).status_code == 403
    assert tick(client, ws_a.id, task, "reopen").status_code == 403
    assert client.get(tasks_url(ws_a.id)).status_code == 200


def test_task_of_another_workspace_is_404(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    stranger = make_task(ws_b)

    r = tick(client_a, ws_a.id, stranger)

    assert r.status_code == 404
    assert r.json()["code"] == "task_not_found"
    assert tick(client_a, ws_a.id, Task(id=uuid.uuid4())).status_code == 404
    assert client_a.get(tasks_url(ws_b.id)).status_code == 404


def test_task_item_names_the_documents_sender(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "Brief")
    with workspace_context(ws_a.id):
        Document.objects.filter(id=document.id).update(sender="Gemeente Utrecht")
    with_document = make_task(ws_a, "Met brief", document=document)
    without = make_task(ws_a, "Los")

    rows = {row["id"]: row for row in client_a.get(tasks_url(ws_a.id)).json()}

    assert rows[str(with_document.id)]["sender"] == "Gemeente Utrecht"
    assert rows[str(with_document.id)]["document_id"] == str(document.id)
    assert (rows[str(without.id)]["sender"], rows[str(without.id)]["document_id"]) == ("", None)


def test_a_task_says_whether_the_ai_made_it(client_a: Client, ws_a: Workspace) -> None:
    by_hand = make_task(ws_a, "Zelf gemaakt")
    by_ai = make_task(ws_a, "Door de AI")
    with workspace_context(ws_a.id):
        Task.objects.filter(id=by_ai.id).update(created_by_ai=True)

    r = client_a.get(tasks_url(ws_a.id))

    assert r.status_code == 200
    flags = {row["id"]: row["created_by_ai"] for row in r.json()}
    assert flags == {str(by_hand.id): False, str(by_ai.id): True}
    done = tick(client_a, ws_a.id, by_ai)
    assert done.json()["created_by_ai"] is True

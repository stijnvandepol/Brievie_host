"""\"Brief afhandelen\" (plan Task 7, spec S 7): the server checks the confirmation."""

from datetime import date
from typing import TYPE_CHECKING

from django.test import Client

from apps.documents.models import Document, DocumentEvent
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.documents_support import bearer_for
from tests.helpers import post_json
from tests.letter_status_support import T, kinds, letter_with_tasks, stored
from tests.my_tasks_support import Household, household
from tests.visibility_support import as_actor, restrict_raw

if TYPE_CHECKING:
    from tests.helpers import Response


def _handle(
    client: Client,
    h: Household,
    document_id: object,
    task_ids: list[object],
    *,
    confirmed: bool = False,
    headers: dict[str, str] | None = None,
) -> "Response":
    return post_json(
        client,
        f"/workspaces/{h.workspace.id}/documents/{document_id}/handle",
        {"task_ids": [str(i) for i in task_ids], "payments_confirmed": confirmed},
        headers=headers,
    )


def _two_tasks(h: Household) -> tuple[Document, list[Task]]:
    document, tasks = letter_with_tasks(
        h.workspace,
        [T(type="respond", description="Reageer"), T(type="respond", description="Bel terug")],
        on=[h.persons["Anne"]],
    )
    return document, tasks


def test_handle_completes_exactly_the_shown_tasks(client: Client) -> None:
    h = household()
    document, tasks = _two_tasks(h)
    client.force_login(h.anne)
    r = _handle(client, h, document.id, [t.id for t in tasks])
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["letter_status"] == "handled"
    assert {t["status"] for t in body["tasks"]} == {"done"}
    assert stored(document.id, h.workspace).open_count == 0


def test_handle_writes_user_line_and_no_system_line(client: Client) -> None:
    h = household()
    document, tasks = _two_tasks(h)
    client.force_login(h.anne)
    _handle(client, h, document.id, [t.id for t in tasks])
    assert kinds(document.id, h.workspace) == [
        "task_completed",
        "task_completed",
        "letter_handled",
    ]
    with as_actor(h.workspace, SYSTEM):
        line = DocumentEvent.objects.get(document_id=document.id, kind="letter_handled")
    assert (line.origin, line.actor_id) == ("user", h.anne.id)


def test_tasks_changed_when_a_task_was_added_or_ticked(client: Client) -> None:
    h = household()
    document, tasks = _two_tasks(h)
    client.force_login(h.anne)
    r = _handle(client, h, document.id, [tasks[0].id])
    assert (r.status_code, r.json()["code"]) == (409, "tasks_changed")
    r = _handle(client, h, document.id, [])
    assert (r.status_code, r.json()["code"]) == (409, "tasks_changed")
    with as_actor(h.workspace, SYSTEM):
        assert Task.objects.filter(document_id=document.id, status="open").count() == 2
    assert kinds(document.id, h.workspace) == []


def test_payment_needs_confirmation(client: Client) -> None:
    h = household()
    document, (pay,) = letter_with_tasks(
        h.workspace, [T(type="pay", description="Betaal")], on=[h.persons["Anne"]]
    )
    client.force_login(h.anne)
    r = _handle(client, h, document.id, [pay.id])
    assert (r.status_code, r.json()["code"]) == (409, "payment_not_confirmed")
    assert r.json()["message"] == "Vink aan dat de betaling is gedaan of geregeld."
    assert stored(document.id, h.workspace).open_count == 1
    assert _handle(client, h, document.id, [pay.id], confirmed=True).status_code == 200
    assert stored(document.id, h.workspace).open_count == 0


def test_waiting_tasks_are_handled_too(client: Client) -> None:
    h = household()
    document, (waiting,) = letter_with_tasks(
        h.workspace, [T(waiting_until=date(2099, 1, 1))], on=[h.persons["Anne"]]
    )
    client.force_login(h.anne)
    assert _handle(client, h, document.id, [waiting.id]).status_code == 200
    with as_actor(h.workspace, SYSTEM):
        done = Task.objects.get(id=waiting.id)
    assert (done.status, done.waiting_until) == ("done", None)


def test_not_reviewed_is_409(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(
        h.workspace, [T()], on=[h.persons["Anne"]], status="needs_review"
    )
    client.force_login(h.anne)
    r = _handle(client, h, document.id, [task.id])
    assert (r.status_code, r.json()["code"]) == (409, "document_not_reviewed")


def test_already_handled_empty_ids_is_noop(client: Client) -> None:
    h = household()
    document, _tasks = letter_with_tasks(h.workspace, [T(status="done")], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    r = _handle(client, h, document.id, [])
    assert r.status_code == 200
    assert r.json()["letter_status"] == "handled"
    assert kinds(document.id, h.workspace) == []


def test_viewer_is_403(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    Membership.objects.filter(workspace=h.workspace, user=h.piet).update(role="viewer")
    client.force_login(h.piet)
    r = _handle(client, h, document.id, [task.id])
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


def test_hidden_letter_is_404(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    restrict_raw(document, [h.anne])
    client.force_login(h.piet)
    r = _handle(client, h, document.id, [task.id])
    assert (r.status_code, r.json()["code"]) == (404, "document_not_found")
    assert stored(document.id, h.workspace).open_count == 1


def test_device_token_can_handle(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    r = _handle(client, h, document.id, [task.id], headers=bearer_for(h.anne))
    assert r.status_code == 200, r.content


def test_completing_deletes_every_snooze_of_the_task(client: Client) -> None:
    from apps.tasks.models import TaskSnooze

    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        TaskSnooze.objects.create(
            workspace=h.workspace, task=task, user=h.piet, until=date(2099, 1, 1)
        )
    client.force_login(h.anne)
    assert _handle(client, h, document.id, [task.id]).status_code == 200
    with as_actor(h.workspace, SYSTEM):
        assert TaskSnooze.objects.count() == 0

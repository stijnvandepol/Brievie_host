"""Take a task, hand it over, release it (spec S 5.3, S 15; plan Task 7)."""

import uuid
from typing import TYPE_CHECKING

from django.test import Client

from apps.accounts.models import User
from apps.documents.models import Document, DocumentAddressee, DocumentEvent
from apps.tasks.assignment import release_unseeing_assignees
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.my_tasks_support import counts, household, ids, letter, mine, task_of
from tests.visibility_support import Office, as_actor, office

if TYPE_CHECKING:
    from tests.helpers import Response


def _assign(client: Client, workspace_id: object, task: Task, user: User | None) -> "Response":
    return post_json(
        client,
        f"/workspaces/{workspace_id}/tasks/{task.id}/assign",
        {"user_id": str(user.id) if user is not None else None},
    )


def _stored(workspace_id: uuid.UUID, task: Task) -> Task:
    with workspace_context(workspace_id, actor=SYSTEM):
        return Task.objects.select_related("assigned_to", "assigned_by").get(id=task.id)


def _events(workspace_id: uuid.UUID, task: Task) -> list[DocumentEvent]:
    with workspace_context(workspace_id, actor=SYSTEM):
        return list(
            DocumentEvent.objects.filter(
                document__tasks__id=task.id,
                kind__in=["task_assigned", "task_unassigned"],
            ).order_by("created_at", "id")
        )


def test_assign_to_self(client: Client) -> None:
    h = household()
    task = letter(h.workspace)
    client.force_login(h.bas)
    r = _assign(client, h.workspace.id, task, h.bas)
    assert r.status_code == 200, r.json()
    assert r.json()["assigned_to_id"] == str(h.bas.id)
    assert r.json()["assigned_to_name"] == "Bas"
    assert r.json()["assigned_by_name"] == "Bas"
    stored = _stored(h.workspace.id, task)
    assert stored.assigned_to_id == stored.assigned_by_id == h.bas.id
    (event,) = _events(h.workspace.id, task)
    assert (event.kind, event.origin, event.actor_id, event.person_name) == (
        "task_assigned",
        "user",
        h.bas.id,
        "Bas",
    )
    # Now Bas's, not Anne's: it left her list and joined his.
    assert task.id in ids(mine(h.workspace, h.bas))
    assert task.id not in ids(mine(h.workspace, h.anne))


def test_hand_over_to_a_reader(client: Client) -> None:
    h = household()
    task = letter(h.workspace, on=[h.persons["Anne"]])
    client.force_login(h.anne)
    r = _assign(client, h.workspace.id, task, h.piet)
    assert r.status_code == 200, r.json()
    assert task.id in ids(mine(h.workspace, h.piet))
    assert {i.task.id: i.reason for i in mine(h.workspace, h.piet).items}[task.id] == "assigned"


def test_assign_to_non_reader_is_422(client: Client) -> None:
    o = office()  # E off: Hans (HR) may not read Femke's letter
    task = task_of(o.workspace, o.letters["scanned_in_review"])
    client.force_login(o.finance)
    r = _assign(client, o.workspace.id, task, o.hr)
    assert r.status_code == 422
    assert r.json()["code"] == "assignee_cannot_read"
    assert _stored(o.workspace.id, task).assigned_to_id is None
    assert _events(o.workspace.id, task) == []


def test_assign_to_viewer_is_422(client: Client) -> None:
    o = office()
    o.state(e=True, d=False, a=True)  # Vera reads everything, but is a viewer
    task = task_of(o.workspace, o.letters["own_piet"])
    client.force_login(o.plain)
    r = _assign(client, o.workspace.id, task, o.viewer)
    assert r.status_code == 422
    assert r.json()["code"] == "assignee_cannot_read"


def test_assign_to_non_member_is_422(client: Client) -> None:
    h = household()
    task = letter(h.workspace)
    stranger = UserFactory.create(name="Vreemd")
    client.force_login(h.anne)
    r = _assign(client, h.workspace.id, task, stranger)
    assert r.status_code == 422
    assert r.json()["code"] == "assignee_cannot_read"


def test_viewer_cannot_assign(client: Client) -> None:
    o = office()
    o.state(e=True, d=False, a=True)
    task = task_of(o.workspace, o.letters["own_piet"])
    client.force_login(o.viewer)
    assert _assign(client, o.workspace.id, task, o.viewer).status_code == 403
    assert (
        client.get(f"/api/v1/workspaces/{o.workspace.id}/tasks/{task.id}/assignees").status_code
        == 403
    )


def test_hidden_task_is_404_like_unknown(client: Client) -> None:
    o = office()
    task = task_of(o.workspace, o.letters["finance_letter"])
    client.force_login(o.hr)
    hidden = _assign(client, o.workspace.id, task, o.hr)
    unknown = post_json(
        client, f"/workspaces/{o.workspace.id}/tasks/{uuid.uuid4()}/assign", {"user_id": None}
    )
    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    listed = client.get(f"/api/v1/workspaces/{o.workspace.id}/tasks/{task.id}/assignees")
    assert listed.status_code == 404


def test_letterless_task_is_422(client: Client) -> None:
    h = household()
    with workspace_context(h.workspace.id, actor=SYSTEM):
        task = Task.objects.create(
            workspace=h.workspace, type="other", description="Los", created_by=h.anne
        )
    client.force_login(h.anne)
    r = _assign(client, h.workspace.id, task, h.anne)
    assert r.status_code == 422
    assert r.json()["code"] == "task_without_letter"


def test_release_writes_task_unassigned(client: Client) -> None:
    h = household()
    task = letter(h.workspace)
    client.force_login(h.anne)
    assert _assign(client, h.workspace.id, task, h.anne).status_code == 200
    r = _assign(client, h.workspace.id, task, None)
    assert r.status_code == 200
    assert r.json()["assigned_to_id"] is None
    stored = _stored(h.workspace.id, task)
    assert stored.assigned_to_id is None
    assert stored.assigned_by_id is None
    kinds = [(e.kind, e.person_name, e.actor_id) for e in _events(h.workspace.id, task)]
    assert kinds == [("task_assigned", "Anne", h.anne.id), ("task_unassigned", "Anne", h.anne.id)]


def test_repeat_assign_writes_no_second_event(client: Client) -> None:
    h = household()
    task = letter(h.workspace)
    client.force_login(h.anne)
    for _ in range(2):
        assert _assign(client, h.workspace.id, task, h.piet).status_code == 200
    assert len(_events(h.workspace.id, task)) == 1
    nobodys = letter(h.workspace)
    assert _assign(client, h.workspace.id, nobodys, None).status_code == 200
    assert _events(h.workspace.id, nobodys) == []


def test_assignees_lists_only_readers_without_viewers(client: Client) -> None:
    o = office()  # E off, D on
    task = task_of(o.workspace, o.letters["finance_letter"])
    client.force_login(o.finance)
    r = client.get(f"/api/v1/workspaces/{o.workspace.id}/tasks/{task.id}/assignees")
    assert r.status_code == 200
    names = [row["name"] for row in r.json()]
    assert names == ["Femke"]
    assert r.json()[0]["is_me"] is True

    o.state(e=True, d=False, a=True)
    r = client.get(f"/api/v1/workspaces/{o.workspace.id}/tasks/{task.id}/assignees")
    assert [row["name"] for row in r.json()] == ["Anne", "Femke", "Hans", "Piet", "Sandra"]


def _hand_finance_letter_to_hr(o: Office) -> None:
    letter_ = o.letters["finance_letter"]
    with as_actor(o.workspace, SYSTEM):
        DocumentAddressee.objects.filter(document=letter_).update(person=o.persons["HR"])
        Document.objects.filter(id=letter_.id).update(addressee_ids=[o.persons["HR"].id])


def test_release_unseeing_assignees_after_hand_over() -> None:
    o = office()
    task = task_of(o.workspace, o.letters["finance_letter"])
    with as_actor(o.workspace, SYSTEM):
        Task.objects.filter(id=task.id).update(assigned_to=o.finance, assigned_by=o.finance)
    _hand_finance_letter_to_hr(o)
    with as_actor(o.workspace, SYSTEM):
        released = release_unseeing_assignees(workspace_id=o.workspace.id)
    assert released == 1
    assert _stored(o.workspace.id, task).assigned_to_id is None
    (event,) = _events(o.workspace.id, task)
    assert (event.kind, event.origin, event.actor_id, event.person_name) == (
        "task_unassigned",
        "system",
        None,
        "Femke",
    )
    with as_actor(o.workspace, SYSTEM):
        assert release_unseeing_assignees(workspace_id=o.workspace.id) == 0


def test_assignee_who_lost_sight_sees_nothing(client: Client) -> None:
    o = office()
    task = task_of(o.workspace, o.letters["finance_letter"])
    with as_actor(o.workspace, SYSTEM):
        Task.objects.filter(id=task.id).update(assigned_to=o.finance)
    _hand_finance_letter_to_hr(o)
    for part in ("mine", "to_pick_up", "all"):
        assert task.id not in ids(mine(o.workspace, o.finance, part=part))
    assert counts(o.workspace, o.finance).mine == 2  # her own two letters only
    client.force_login(o.finance)
    r = post_json(client, f"/workspaces/{o.workspace.id}/tasks/{task.id}/complete")
    assert r.status_code == 404

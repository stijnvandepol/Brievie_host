"""Tasks follow their letter; a letterless task its maker and assignee (S 4.9.3, 4.10,
R39, plan Task 15).

Every letter of `office()` carries one open task. In own post Hans (HR) sees the task of
HR's letter only; a task without a letter is seen by its maker, its assignee, whoever
sees all post, and the system.
"""

import uuid

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.documents.models import Document
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.visibility_support import Office, as_actor, office


def _tasks_url(o: Office) -> str:
    return f"/api/v1/workspaces/{o.workspace.id}/tasks"


def _listed(o: Office, client: Client, user: User) -> set[uuid.UUID]:
    client.force_login(user)
    response = client.get(f"{_tasks_url(o)}?status=all")
    assert response.status_code == 200
    return {uuid.UUID(item["id"]) for item in response.json()}


def _task_of(o: Office, name: str) -> Task:
    with as_actor(o.workspace, SYSTEM):
        return Task.objects.get(document=o.letters[name])


def _letterless(
    o: Office, *, created_by: User | None = None, assigned_to: User | None = None
) -> Task:
    with as_actor(o.workspace, SYSTEM):
        return Task.objects.create(
            workspace=o.workspace,
            document=None,
            type="other",
            description="Losse taak",
            created_by=created_by,
            assigned_to=assigned_to,
        )


def test_list_tasks_omits_tasks_of_hidden_letters(client: Client) -> None:
    o = office()
    assert _listed(o, client, o.hr) == {_task_of(o, "hr_letter").id}
    for user in (o.plain, o.finance, o.sorter, o.admin, o.viewer):
        expected = {_task_of(o, name).id for name in o.visible(user.id)}
        assert _listed(o, client, user) == expected, user.name
    assert _task_of(o, "finance_letter").id in _listed(o, client, o.finance)


@pytest.mark.parametrize("action", ["complete", "reopen"])
def test_complete_and_reopen_a_hidden_task_are_404(client: Client, action: str) -> None:
    o = office()
    task = _task_of(o, "finance_letter")
    if action == "reopen":
        with as_actor(o.workspace, SYSTEM):
            Task.objects.filter(id=task.id).update(status=Task.Status.DONE)
    client.force_login(o.hr)

    hidden = client.post(f"{_tasks_url(o)}/{task.id}/{action}")
    unknown = client.post(f"{_tasks_url(o)}/{uuid.uuid4()}/{action}")

    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json() == unknown.json()
    assert hidden.json()["code"] == "task_not_found"
    with as_actor(o.workspace, SYSTEM):
        stored = Task.objects.get(id=task.id)
    assert stored.status == (Task.Status.OPEN if action == "complete" else Task.Status.DONE)


def test_letterless_task_visible_to_its_maker_only(client: Client) -> None:
    o = office()
    task = _letterless(o, created_by=o.plain)
    assert task.id in _listed(o, client, o.plain)
    for user in (o.hr, o.finance, o.sorter, o.admin, o.viewer):
        assert task.id not in _listed(o, client, user), user.name
    client.force_login(o.hr)
    assert client.post(f"{_tasks_url(o)}/{task.id}/complete").status_code == 404
    client.force_login(o.plain)
    assert client.post(f"{_tasks_url(o)}/{task.id}/complete").status_code == 200


def test_letterless_task_visible_to_its_assignee(client: Client) -> None:
    o = office()
    task = _letterless(o, created_by=o.plain, assigned_to=o.hr)
    assert task.id in _listed(o, client, o.hr)
    assert task.id in _listed(o, client, o.plain)
    assert task.id not in _listed(o, client, o.finance)


def test_letterless_task_without_maker_visible_when_everyone_sees_all(client: Client) -> None:
    o = office()
    task = _letterless(o)
    for user in o.users.values():
        assert task.id not in _listed(o, client, user), user.name

    o.state(e=False, d=True, a=True)
    assert task.id in _listed(o, client, o.admin)
    assert task.id not in _listed(o, client, o.hr)

    o.state(e=True, d=False, a=True)
    for user in o.users.values():
        assert task.id in _listed(o, client, user), user.name


def test_task_of_a_deleted_letter_follows_the_letterless_rule(client: Client) -> None:
    o = office()
    task = _task_of(o, "hr_letter")
    assert task.id in _listed(o, client, o.hr)
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=o.letters["hr_letter"].id).delete()
        assert Task.objects.get(id=task.id).document_id is None
    assert task.id not in _listed(o, client, o.hr)


def test_an_assignee_on_a_letter_task_does_not_widen_it(client: Client) -> None:
    """Only a letterless task is seen through its assignee: a task on a letter follows the
    letter, so an assignment never shows a letter's task to someone who cannot read it."""
    o = office()
    task = _task_of(o, "finance_letter")
    with as_actor(o.workspace, SYSTEM):
        Task.objects.filter(id=task.id).update(assigned_to=o.hr, created_by=o.hr)
    assert task.id not in _listed(o, client, o.hr)

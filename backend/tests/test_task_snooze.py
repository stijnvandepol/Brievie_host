"""\"Uitgesteld tot\" (plan Task 9, spec S 5): personal, never on the letter."""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.tasks import services
from apps.tasks.models import Task, TaskSnooze
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.helpers import post_json
from tests.letter_status_support import T, kinds, letter_with_tasks, stored
from tests.my_tasks_support import Household, household
from tests.visibility_support import as_actor

if TYPE_CHECKING:
    from tests.helpers import Response

TODAY = date(2026, 11, 24)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    def fixed(now: datetime | None = None) -> date:
        return TODAY

    monkeypatch.setattr(services, "task_today", fixed)


def _snooze(client: Client, h: Household, task: Task, until: date | None) -> "Response":
    value = until.isoformat() if until else None
    return post_json(
        client, f"/workspaces/{h.workspace.id}/tasks/{task.id}/snooze", {"until": value}
    )


def _mine(client: Client, h: Household) -> list[str]:
    r = client.get(f"/api/v1/workspaces/{h.workspace.id}/tasks")
    return [row["id"] for row in r.json()]


def test_snooze_is_stored_and_shown_to_the_member_only() -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    anne, piet = Client(), Client()
    anne.force_login(h.anne)
    piet.force_login(h.piet)
    until = TODAY + timedelta(days=7)
    r = _snooze(anne, h, task, until)
    assert r.status_code == 200, r.content
    assert r.json()["snoozed_until"] == until.isoformat()
    rows = {row["id"]: row for row in piet.get(f"/api/v1/workspaces/{h.workspace.id}/tasks").json()}
    assert rows[str(task.id)]["snoozed_until"] is None
    rows = {row["id"]: row for row in anne.get(f"/api/v1/workspaces/{h.workspace.id}/tasks").json()}
    assert rows[str(task.id)]["snoozed_until"] == until.isoformat()


def test_snooze_does_not_change_letter_status_or_history(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    before = stored(document.id, h.workspace)
    client.force_login(h.anne)
    _snooze(client, h, task, TODAY + timedelta(days=3))
    assert stored(document.id, h.workspace) == before
    assert kinds(document.id, h.workspace) == []


def test_snooze_not_after_deadline(client: Client) -> None:
    h = household()
    _, (task,) = letter_with_tasks(
        h.workspace, [T(deadline=TODAY + timedelta(days=10))], on=[h.persons["Anne"]]
    )
    client.force_login(h.anne)
    assert _snooze(client, h, task, TODAY + timedelta(days=10)).status_code == 200
    r = _snooze(client, h, task, TODAY + timedelta(days=11))
    assert (r.status_code, r.json()["code"]) == (422, "snooze_after_deadline")
    assert r.json()["message"] == "Je kunt niet uitstellen tot na de datum van de taak."


def test_overdue_task_cannot_be_snoozed(client: Client) -> None:
    h = household()
    _, (task,) = letter_with_tasks(
        h.workspace, [T(deadline=TODAY - timedelta(days=2))], on=[h.persons["Anne"]]
    )
    client.force_login(h.anne)
    r = _snooze(client, h, task, TODAY + timedelta(days=1))
    assert (r.status_code, r.json()["code"]) == (422, "snooze_after_deadline")


@pytest.mark.parametrize(("offset", "ok"), [(0, False), (1, True), (365, True), (366, False)])
def test_snooze_date_bounds(client: Client, offset: int, ok: bool) -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    r = _snooze(client, h, task, TODAY + timedelta(days=offset))
    assert r.status_code == (200 if ok else 422)


def test_unsnooze(client: Client) -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    _snooze(client, h, task, TODAY + timedelta(days=3))
    r = _snooze(client, h, task, None)
    assert r.status_code == 200
    assert r.json()["snoozed_until"] is None
    with as_actor(h.workspace, SYSTEM):
        assert TaskSnooze.objects.count() == 0


def test_snooze_again_replaces_the_day(client: Client) -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    _snooze(client, h, task, TODAY + timedelta(days=3))
    _snooze(client, h, task, TODAY + timedelta(days=5))
    with as_actor(h.workspace, SYSTEM):
        assert list(TaskSnooze.objects.values_list("until", flat=True)) == [
            TODAY + timedelta(days=5)
        ]


def test_viewer_may_snooze(client: Client) -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    Membership.objects.filter(workspace=h.workspace, user=h.piet).update(role="viewer")
    client.force_login(h.piet)
    assert _snooze(client, h, task, TODAY + timedelta(days=3)).status_code == 200


def test_done_task_is_409(client: Client) -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T(status="done")], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    r = _snooze(client, h, task, TODAY + timedelta(days=3))
    assert (r.status_code, r.json()["code"]) == (409, "task_done")


def test_completion_deletes_snoozes(client: Client) -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    piet = Client()
    piet.force_login(h.piet)
    _snooze(piet, h, task, TODAY + timedelta(days=3))
    client.force_login(h.anne)
    assert (
        post_json(client, f"/workspaces/{h.workspace.id}/tasks/{task.id}/complete").status_code
        == 200
    )
    with as_actor(h.workspace, SYSTEM):
        assert TaskSnooze.objects.count() == 0


def test_the_letter_shows_only_my_snooze(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    piet = Client()
    piet.force_login(h.piet)
    until = TODAY + timedelta(days=3)
    _snooze(piet, h, task, until)
    client.force_login(h.anne)
    url = f"/api/v1/workspaces/{h.workspace.id}/documents/{document.id}"
    assert client.get(url).json()["tasks"][0]["snoozed_until"] is None
    assert piet.get(url).json()["tasks"][0]["snoozed_until"] == until.isoformat()


def test_a_hidden_task_is_404(client: Client) -> None:
    from tests.visibility_support import restrict_raw

    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    restrict_raw(document, [h.anne])
    client.force_login(h.piet)
    r = _snooze(client, h, task, TODAY + timedelta(days=3))
    assert (r.status_code, r.json()["code"]) == (404, "task_not_found")


def test_reopening_a_letter_is_not_blocked_by_anothers_snooze(client: Client) -> None:
    from apps.documents import services as document_services
    from apps.documents.models import Document

    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        Task.objects.filter(id=task.id).update(created_by_ai=True)
        Document.objects.filter(id=document.id).update(confirmed_by_ai=True)
        TaskSnooze.objects.create(
            workspace=h.workspace, task=task, user=h.piet, until=TODAY + timedelta(days=3)
        )
    with as_actor(h.workspace, h.anne.id):
        document_services.reopen_document(
            workspace=h.workspace, user=h.anne, document_id=document.id
        )
    with as_actor(h.workspace, SYSTEM):
        assert not Task.objects.filter(id=task.id).exists()
        assert TaskSnooze.objects.count() == 0


def test_deleting_a_task_as_a_member_takes_every_snooze_with_it() -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        TaskSnooze.objects.create(
            workspace=h.workspace, task=task, user=h.piet, until=TODAY + timedelta(days=3)
        )
    with as_actor(h.workspace, h.anne.id):
        Task.objects.filter(id=task.id).delete()
    with as_actor(h.workspace, SYSTEM):
        assert TaskSnooze.objects.count() == 0


def test_a_member_who_leaves_loses_their_snoozes() -> None:
    from apps.documents import visibility

    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        TaskSnooze.objects.create(
            workspace=h.workspace, task=task, user=h.piet, until=TODAY + timedelta(days=3)
        )
        TaskSnooze.objects.create(
            workspace=h.workspace, task=task, user=h.anne, until=TODAY + timedelta(days=3)
        )
    visibility.forget_member(h.workspace.id, h.piet.id)
    with as_actor(h.workspace, SYSTEM):
        assert list(TaskSnooze.objects.values_list("user_id", flat=True)) == [h.anne.id]


def test_snoozing_takes_no_row_lock(client: Client) -> None:
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    client.force_login(h.piet)
    with CaptureQueriesContext(connection) as queries:
        assert _snooze(client, h, task, TODAY + timedelta(days=3)).status_code == 200
        assert _snooze(client, h, task, None).status_code == 200
    assert not [q["sql"] for q in queries if " FOR " in q["sql"]]

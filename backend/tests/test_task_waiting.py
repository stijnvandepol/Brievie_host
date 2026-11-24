"""\"Wacht op reactie\" (plan Task 8, spec S 5): shared, dated, one line in the history."""

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.documents.models import DocumentEvent
from apps.tasks import services
from apps.tasks.models import Task
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


def _wait(
    client: Client, h: Household, task: Task, until: date | None, *, raw: str | None = None
) -> "Response":
    value = raw if raw is not None else (until.isoformat() if until else None)
    return post_json(
        client, f"/workspaces/{h.workspace.id}/tasks/{task.id}/waiting", {"until": value}
    )


def test_wait_moves_letter_to_waiting_and_writes_line(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    until = TODAY + timedelta(days=10)
    r = _wait(client, h, task, until)
    assert r.status_code == 200, r.content
    assert r.json()["waiting_until"] == until.isoformat()
    assert r.json()["wait_over"] is False
    assert stored(document.id, h.workspace).wait_until == until
    assert kinds(document.id, h.workspace) == ["task_waiting"]
    with as_actor(h.workspace, SYSTEM):
        line = DocumentEvent.objects.get(document=document)
    assert (line.on_date, line.actor_id, line.origin) == (until, h.anne.id, "user")


def test_wait_on_one_of_two_tasks_keeps_letter_open(client: Client) -> None:
    h = household()
    document, (first, _second) = letter_with_tasks(h.workspace, [T(), T()], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    assert _wait(client, h, first, TODAY + timedelta(days=5)).status_code == 200
    assert stored(document.id, h.workspace).wait_until is None
    rows = client.get(f"/api/v1/workspaces/{h.workspace.id}/documents").json()
    assert rows[0]["letter_status"] == "open"


def test_stop_waiting_writes_ended_line(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(
        h.workspace, [T(waiting_until=TODAY + timedelta(days=3))], on=[h.persons["Anne"]]
    )
    client.force_login(h.anne)
    r = _wait(client, h, task, None)
    assert r.status_code == 200
    assert r.json()["waiting_until"] is None
    assert kinds(document.id, h.workspace) == ["task_waiting_ended"]
    assert stored(document.id, h.workspace).wait_until is None


@pytest.mark.parametrize(
    ("offset", "ok"), [(0, False), (-3, False), (1, True), (365, True), (366, False)]
)
def test_wait_date_must_be_after_today_within_a_year(client: Client, offset: int, ok: bool) -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    r = _wait(client, h, task, TODAY + timedelta(days=offset))
    if ok:
        assert r.status_code == 200
    else:
        assert (r.status_code, r.json()["code"]) == (422, "validation_error")
        assert r.json()["message"] == "Kies een datum na vandaag, binnen een jaar."


def test_wait_may_pass_the_deadline(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(
        h.workspace, [T(deadline=date(2026, 11, 30))], on=[h.persons["Anne"]]
    )
    client.force_login(h.anne)
    assert _wait(client, h, task, date(2026, 12, 20)).status_code == 200
    facts = stored(document.id, h.workspace)
    assert facts.wait_until == date(2026, 12, 20)
    assert facts.overdue_from == date(2026, 12, 20)


def test_same_date_again_writes_nothing(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    until = TODAY + timedelta(days=4)
    _wait(client, h, task, until)
    _wait(client, h, task, until)
    assert kinds(document.id, h.workspace) == ["task_waiting"]


def test_completing_clears_waiting(client: Client) -> None:
    h = household()
    document, (task,) = letter_with_tasks(
        h.workspace, [T(waiting_until=TODAY + timedelta(days=4))], on=[h.persons["Anne"]]
    )
    client.force_login(h.anne)
    r = post_json(client, f"/workspaces/{h.workspace.id}/tasks/{task.id}/complete")
    assert r.json()["waiting_until"] is None
    assert stored(document.id, h.workspace).open_count == 0
    r = _wait(client, h, task, TODAY + timedelta(days=4))
    assert (r.status_code, r.json()["code"]) == (409, "task_done")
    assert r.json()["message"] == "Deze taak is al afgerond."


def test_wait_over_flag_when_date_arrives(client: Client) -> None:
    h = household()
    letter_with_tasks(h.workspace, [T(waiting_until=TODAY)], on=[h.persons["Anne"]])
    client.force_login(h.anne)
    rows = client.get(f"/api/v1/workspaces/{h.workspace.id}/tasks").json()
    assert [(r["waiting_until"], r["wait_over"]) for r in rows] == [(TODAY.isoformat(), True)]


def test_viewer_cannot_set_waiting(client: Client) -> None:
    h = household()
    _, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    Membership.objects.filter(workspace=h.workspace, user=h.piet).update(role="viewer")
    client.force_login(h.piet)
    r = _wait(client, h, task, TODAY + timedelta(days=2))
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


def test_a_hidden_task_is_404(client: Client) -> None:
    from tests.visibility_support import restrict_raw

    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    restrict_raw(document, [h.anne])
    client.force_login(h.piet)
    r = _wait(client, h, task, TODAY + timedelta(days=2))
    assert (r.status_code, r.json()["code"]) == (404, "task_not_found")

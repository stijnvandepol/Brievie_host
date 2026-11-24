"""Letter status on the lists, the filter and the counts (plan Task 5, spec S 4, 9)."""

from datetime import date, datetime

import pytest
from django.test import Client

from apps.tasks import services
from tests.documents_support import bearer_for, trashed
from tests.letter_status_support import T, letter_with_tasks
from tests.my_tasks_support import Household, household

TODAY = date(2026, 11, 24)
PAST = date(2026, 11, 1)
FUTURE = date(2026, 12, 15)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    def fixed(now: datetime | None = None) -> date:
        return TODAY

    monkeypatch.setattr(services, "task_today", fixed)


def _documents(h: Household) -> str:
    return f"/api/v1/workspaces/{h.workspace.id}/documents"


def _world(h: Household) -> dict[str, str]:
    """One letter per status, plus a late one; the names map to the letter ids."""
    anne = [h.persons["Anne"]]
    made = {
        "new": letter_with_tasks(h.workspace, [T()], on=anne, status="needs_review")[0],
        "handled": letter_with_tasks(h.workspace, [T(status="done")], on=anne)[0],
        "open": letter_with_tasks(h.workspace, [T(deadline=FUTURE)], on=anne)[0],
        "late": letter_with_tasks(h.workspace, [T(deadline=PAST)], on=anne)[0],
        "waiting": letter_with_tasks(h.workspace, [T(waiting_until=FUTURE)], on=anne)[0],
        "over": letter_with_tasks(h.workspace, [T(waiting_until=PAST)], on=anne)[0],
        "other": letter_with_tasks(h.workspace, [T()], on=[h.persons["Piet"]])[0],
    }
    return {name: str(document.id) for name, document in made.items()}


def _ids(client: Client, h: Household, query: str) -> set[str]:
    r = client.get(f"{_documents(h)}?{query}")
    assert r.status_code == 200, r.content
    return {row["id"] for row in r.json()}


def test_summary_carries_status_and_overdue(client: Client) -> None:
    h = household()
    ids = _world(h)
    client.force_login(h.anne)
    rows = {row["id"]: row for row in client.get(_documents(h)).json()}
    assert rows[ids["new"]]["letter_status"] == "new"
    assert rows[ids["handled"]]["letter_status"] == "handled"
    assert rows[ids["open"]]["letter_status"] == "open"
    assert rows[ids["open"]]["open_task_count"] == 1
    assert rows[ids["open"]]["overdue"] is False
    assert rows[ids["late"]]["letter_status"] == "open"
    assert rows[ids["late"]]["overdue"] is True
    assert rows[ids["waiting"]]["letter_status"] == "waiting"
    assert rows[ids["over"]]["letter_status"] == "open"


def test_filter_each_status(client: Client) -> None:
    h = household()
    ids = _world(h)
    client.force_login(h.anne)
    assert _ids(client, h, "letter_status=new") == {ids["new"]}
    assert _ids(client, h, "letter_status=handled") == {ids["handled"]}
    assert _ids(client, h, "letter_status=waiting") == {ids["waiting"]}
    assert _ids(client, h, "letter_status=open") == {
        ids["open"],
        ids["late"],
        ids["over"],
        ids["other"],
    }


def test_overdue_filter(client: Client) -> None:
    h = household()
    ids = _world(h)
    client.force_login(h.anne)
    assert _ids(client, h, "overdue=true") == {ids["late"]}
    assert _ids(client, h, "overdue=true&letter_status=waiting") == set()


def test_unknown_status_is_422(client: Client) -> None:
    h = household()
    client.force_login(h.anne)
    r = client.get(f"{_documents(h)}?letter_status=stuck")
    assert r.status_code == 422
    assert r.json() == {"code": "validation_error", "message": "Deze status kennen we niet."}


def test_counts_for_person_and_for_all(client: Client) -> None:
    h = household()
    _world(h)
    client.force_login(h.anne)
    base = f"/api/v1/workspaces/{h.workspace.id}/documents/status-counts"
    assert client.get(base).json() == {
        "new": 1,
        "open": 4,
        "waiting": 1,
        "handled": 1,
        "overdue": 1,
    }
    own = client.get(f"{base}?person_id={h.persons['Anne'].id}").json()
    assert own == {"new": 1, "open": 3, "waiting": 1, "handled": 1, "overdue": 1}


def test_counts_unknown_person_are_zero(client: Client) -> None:
    import uuid

    h = household()
    _world(h)
    client.force_login(h.anne)
    r = client.get(
        f"/api/v1/workspaces/{h.workspace.id}/documents/status-counts?person_id={uuid.uuid4()}"
    )
    assert r.status_code == 200
    assert r.json() == {"new": 0, "open": 0, "waiting": 0, "handled": 0, "overdue": 0}


def test_waiting_until_only_when_waiting(client: Client) -> None:
    h = household()
    ids = _world(h)
    client.force_login(h.anne)
    rows = {row["id"]: row for row in client.get(_documents(h)).json()}
    assert rows[ids["waiting"]]["waiting_until"] == FUTURE.isoformat()
    assert rows[ids["over"]]["waiting_until"] is None
    assert rows[ids["open"]]["waiting_until"] is None


def test_waiting_letter_late_again_when_the_date_has_passed(client: Client) -> None:
    h = household()
    document, _ = letter_with_tasks(
        h.workspace, [T(deadline=PAST, waiting_until=date(2026, 11, 10))], on=[h.persons["Anne"]]
    )
    client.force_login(h.anne)
    (row,) = [r for r in client.get(_documents(h)).json() if r["id"] == str(document.id)]
    assert (row["letter_status"], row["overdue"]) == ("open", True)


def test_trashed_letters_are_not_counted(client: Client) -> None:
    h = household()
    ids = _world(h)
    gone, _ = letter_with_tasks(h.workspace, [T()], on=[h.persons["Anne"]])
    trashed(gone)
    client.force_login(h.anne)
    counts = client.get(f"/api/v1/workspaces/{h.workspace.id}/documents/status-counts").json()
    assert counts["open"] == 4
    assert str(gone.id) not in _ids(client, h, "letter_status=open")
    assert ids["open"] in _ids(client, h, "letter_status=open")


def test_device_token_can_list_and_count(client: Client) -> None:
    h = household()
    _world(h)
    headers = bearer_for(h.anne)
    r = client.get(f"{_documents(h)}?letter_status=waiting", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    r = client.get(f"{_documents(h)}/status-counts", headers=headers)
    assert r.status_code == 200
    assert r.json()["waiting"] == 1


def test_a_new_status_is_not_the_unread_marker(client: Client) -> None:
    h = household()
    ids = _world(h)
    client.force_login(h.anne)
    rows = {row["id"]: row for row in client.get(_documents(h)).json()}
    assert "unread" in rows[ids["open"]]
    assert rows[ids["open"]]["letter_status"] == "open"

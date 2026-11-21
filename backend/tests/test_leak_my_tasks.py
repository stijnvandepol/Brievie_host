""" "Mijn taken" follows the rule of sight (spec S 8, Review Focus 1; plan Task 11).

In `office()` with "Iedereen ziet alle post" off and departments on: no member gets an
item, a count, a name list or a waiting-letter count of a letter they may not read, in
any part and either status; trash and restore move tasks out and back; a private letter's
task reaches only its readers; no log line names a task.
"""

import logging
import uuid
from datetime import date, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.documents import visibility
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.helpers import post_json
from tests.visibility_support import Office, as_actor, office

PARTS = ("mine", "to_pick_up", "all")
SECRET = "Betaal de deurwaarder geheim"


def _url(o: Office, suffix: str = "") -> str:
    return f"/api/v1/workspaces/{o.workspace.id}/my-tasks{suffix}"


def _letter_tasks(o: Office) -> dict[uuid.UUID, str]:
    """task id -> the name of its letter."""
    with as_actor(o.workspace, SYSTEM):
        rows = Task.objects.filter(document__isnull=False).values_list("id", "document_id")
        return {task_id: o.name_of(document_id) for task_id, document_id in rows}


def _make_every_task_count(o: Office) -> None:
    """Every task overdue with a term, half of them done today: each would show
    everywhere a hidden one could leak (items, every count, "Afgerond")."""
    with as_actor(o.workspace, SYSTEM):
        tasks = list(Task.objects.order_by("id"))
        for index, task in enumerate(tasks):
            task.description = SECRET
            task.deadline = date(2000, 1, 1) if index % 2 else None
            task.term_quote = "binnen zes weken"
            task.save(update_fields=["description", "deadline", "term_quote"])
        Task.objects.filter(id__in=[t.id for t in tasks[::3]]).update(
            status=Task.Status.DONE, completed_at=timezone.now() - timedelta(days=1)
        )


def test_no_item_or_count_of_a_hidden_letter_for_any_member(client: Client) -> None:
    o = office()
    _make_every_task_count(o)
    letters = _letter_tasks(o)
    for user in o.users.values():
        visible = o.visible(user.id)
        with as_actor(o.workspace, SYSTEM):
            visible_open = Task.objects.filter(
                document__in=[o.letters[n] for n in visible], status="open"
            ).count()
        client.force_login(user)
        for part in PARTS:
            for status in ("open", "done"):
                r = client.get(_url(o), {"part": part, "status": status})
                assert r.status_code == 200
                for item in r.json()["items"]:
                    assert letters[uuid.UUID(item["id"])] in visible, (user.name, part, item)
        counted = client.get(_url(o, "/counts")).json()
        assert counted["mine"] + counted["to_pick_up"] <= visible_open, user.name
        for number in ("overdue", "within_week", "needs_date"):
            assert counted[number] <= counted["mine"], (user.name, number)
        all_open = client.get(_url(o), {"part": "all"}).json()["items"]
        assert len(all_open) == visible_open, user.name


def test_hr_gets_nothing_of_finance(client: Client) -> None:
    o = office()
    _make_every_task_count(o)
    finance = next(t for t, n in _letter_tasks(o).items() if n == "finance_letter")
    client.force_login(o.hr)
    for part in PARTS:
        for status in ("open", "done"):
            body = client.get(_url(o), {"part": part, "status": status}).content.decode()
            assert str(finance) not in body
    assert (
        post_json(client, f"/workspaces/{o.workspace.id}/tasks/{finance}/deadline", {}).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/workspaces/{o.workspace.id}/tasks/{finance}/assignees").status_code
        == 404
    )


def test_assignees_never_list_a_non_reader(client: Client) -> None:
    o = office()
    for user in (o.admin, o.sorter, o.finance, o.hr, o.plain):
        client.force_login(user)
        items = client.get(_url(o), {"part": "all"}).json()["items"]
        for item in items:
            if item["document_id"] is None:
                continue
            r = client.get(f"/api/v1/workspaces/{o.workspace.id}/tasks/{item['id']}/assignees")
            assert r.status_code == 200
            with as_actor(o.workspace, SYSTEM):
                readers = visibility.viewers([uuid.UUID(item["document_id"])])
            allowed = readers[uuid.UUID(item["document_id"])]
            for row in r.json():
                assert uuid.UUID(row["user_id"]) in allowed
                assert uuid.UUID(row["user_id"]) != o.viewer.id


def test_for_names_only_on_visible_letters(client: Client) -> None:
    o = office()
    letters = _letter_tasks(o)
    client.force_login(o.hr)
    items = client.get(_url(o), {"part": "all"}).json()["items"]
    assert {letters[uuid.UUID(i["id"])] for i in items} == o.visible(o.hr.id)
    names = {name for i in items for name in i["for_names"]}
    assert names == {"HR"}


def test_waiting_letters_counts_only_visible(client: Client) -> None:
    o = office()
    expected = {o.hr: 0, o.plain: 1, o.finance: 1, o.sorter: 1}
    for user, waiting in expected.items():
        client.force_login(user)
        assert client.get(_url(o)).json()["waiting_letters"] == waiting, user.name


def _in_list(o: Office, client: Client, user: User, task_id: uuid.UUID) -> bool:
    client.force_login(user)
    items = client.get(_url(o), {"part": "all"}).json()["items"]
    return str(task_id) in {i["id"] for i in items}


def test_trash_and_restore_move_tasks(client: Client) -> None:
    o = office()
    letter = o.letters["own_piet"]
    task = next(t for t, n in _letter_tasks(o).items() if n == "own_piet")
    assert _in_list(o, client, o.plain, task)
    client.force_login(o.plain)
    trashed = post_json(client, f"/workspaces/{o.workspace.id}/documents/{letter.id}/trash")
    assert trashed.status_code == 200, trashed.json()
    assert not _in_list(o, client, o.plain, task)
    assert client.get(_url(o, "/counts")).json()["mine"] == 0
    restored = post_json(client, f"/workspaces/{o.workspace.id}/documents/{letter.id}/restore")
    assert restored.status_code == 200, restored.json()
    assert _in_list(o, client, o.plain, task)


@pytest.mark.parametrize("everyone", [False, True])
def test_private_letter_task_only_for_readers(client: Client, everyone: bool) -> None:
    o = office()
    o.state(e=everyone, d=True, a=everyone)
    task = next(t for t, n in _letter_tasks(o).items() if n == "private_femke")
    assert _in_list(o, client, o.finance, task)
    for user in (o.admin, o.sorter, o.hr, o.plain, o.viewer):
        assert not _in_list(o, client, user, task), user.name


def test_no_description_in_logs(caplog: pytest.LogCaptureFixture, client: Client) -> None:
    o = office()
    _make_every_task_count(o)
    caplog.set_level(logging.DEBUG)
    client.force_login(o.finance)
    for part in PARTS:
        client.get(_url(o), {"part": part})
    client.get(_url(o, "/counts"))
    items = client.get(_url(o), {"part": "all"}).json()["items"]
    open_items = [i for i in items if i["status"] == "open"]
    first = open_items[0]["id"]
    post_json(
        client, f"/workspaces/{o.workspace.id}/tasks/{first}/assign", {"user_id": str(o.finance.id)}
    )
    post_json(
        client, f"/workspaces/{o.workspace.id}/tasks/{first}/deadline", {"deadline": "2099-01-01"}
    )
    for record in caplog.records:
        text = record.getMessage()
        assert SECRET not in text
        assert "binnen zes weken" not in text

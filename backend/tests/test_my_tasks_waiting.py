"""\"Mijn taken\" leaves waiting and snoozed tasks out (plan Task 10, spec S 6.3)."""

from datetime import date, datetime, timedelta

import pytest

from apps.accounts.models import User
from apps.tasks import services
from apps.tasks.mine import Group
from apps.tasks.models import Task, TaskSnooze
from apps.workspaces.context import SYSTEM
from tests.letter_status_support import T, letter_with_tasks
from tests.my_tasks_support import Household, counts, household, ids, mine
from tests.visibility_support import as_actor

TODAY = date(2026, 11, 24)
SOON = TODAY + timedelta(days=5)


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    def fixed(now: datetime | None = None) -> date:
        return TODAY

    monkeypatch.setattr(services, "task_today", fixed)


def _task(h: Household, spec: T, *, on: tuple[str, ...] = ("Anne",)) -> Task:
    _, (task,) = letter_with_tasks(h.workspace, [spec], on=[h.persons[name] for name in on])
    return task


def _snooze(h: Household, task: Task, user: User) -> None:
    with as_actor(h.workspace, SYSTEM):
        TaskSnooze.objects.create(workspace=h.workspace, task=task, user=user, until=SOON)


def test_waiting_task_out_of_groups_and_counts() -> None:
    h = household()
    active = _task(h, T(deadline=TODAY + timedelta(days=30)))
    _task(h, T(waiting_until=SOON))
    assert ids(mine(h.workspace, h.anne)) == {active.id}
    c = counts(h.workspace, h.anne)
    assert (c.mine, c.waiting, c.snoozed) == (1, 1, 0)


def test_waiting_over_task_back_in_its_group() -> None:
    h = household()
    over = _task(h, T(deadline=TODAY - timedelta(days=3), waiting_until=TODAY))
    result = mine(h.workspace, h.anne)
    (item,) = result.items
    assert item.task.id == over.id
    assert item.group == Group.OVERDUE
    assert item.task.waiting_until == TODAY


def test_snoozed_task_out_for_me_only() -> None:
    h = household()
    task = _task(h, T(), on=("Anne", "Bas"))
    _snooze(h, task, h.anne)
    assert ids(mine(h.workspace, h.anne)) == set()
    assert ids(mine(h.workspace, h.bas)) == {task.id}
    assert (counts(h.workspace, h.anne).mine, counts(h.workspace, h.anne).snoozed) == (0, 1)
    assert (counts(h.workspace, h.bas).mine, counts(h.workspace, h.bas).snoozed) == (1, 0)


def test_a_snooze_that_has_ended_changes_nothing() -> None:
    h = household()
    task = _task(h, T())
    with as_actor(h.workspace, SYSTEM):
        TaskSnooze.objects.create(
            workspace=h.workspace, task=task, user=h.anne, until=TODAY - timedelta(days=1)
        )
    assert ids(mine(h.workspace, h.anne)) == {task.id}


def test_include_waiting_adds_both_groups() -> None:
    h = household()
    active = _task(h, T())
    waiting = _task(h, T(waiting_until=SOON))
    snoozed = _task(h, T())
    _snooze(h, snoozed, h.anne)
    result = mine(h.workspace, h.anne, include_waiting=True)
    groups = {item.task.id: item.group for item in result.items}
    assert groups == {
        active.id: Group.NO_DATE,
        waiting.id: Group.WAITING,
        snoozed.id: Group.SNOOZED,
    }


def test_without_include_waiting_no_new_group_values() -> None:
    h = household()
    _task(h, T(waiting_until=SOON))
    snoozed = _task(h, T())
    _snooze(h, snoozed, h.anne)
    _task(h, T(deadline=TODAY + timedelta(days=2)))
    groups = {item.group for item in mine(h.workspace, h.anne).items}
    assert groups <= {Group.OVERDUE, Group.WITHIN_WEEK, Group.LATER, Group.NO_DATE}


def test_waiting_and_snoozed_is_waiting() -> None:
    h = household()
    task = _task(h, T(waiting_until=SOON))
    _snooze(h, task, h.anne)
    result = mine(h.workspace, h.anne, include_waiting=True)
    assert [(i.task.id, i.group) for i in result.items] == [(task.id, Group.WAITING)]
    c = counts(h.workspace, h.anne)
    assert (c.waiting, c.snoozed) == (1, 0)


def test_overdue_count_excludes_waiting() -> None:
    h = household()
    _task(h, T(deadline=TODAY - timedelta(days=5)))
    _task(h, T(deadline=TODAY - timedelta(days=5), waiting_until=SOON))
    c = counts(h.workspace, h.anne)
    assert (c.mine, c.overdue, c.waiting) == (1, 1, 1)


def test_counts_match_list_lengths() -> None:
    h = household()
    _task(h, T(deadline=TODAY - timedelta(days=1)))
    _task(h, T(deadline=TODAY + timedelta(days=3)))
    _task(h, T(waiting_until=SOON))
    snoozed = _task(h, T())
    _snooze(h, snoozed, h.anne)
    everything = mine(h.workspace, h.anne, include_waiting=True)
    active = mine(h.workspace, h.anne)
    c = counts(h.workspace, h.anne)
    assert c.mine == len(active.items) == 2
    assert c.waiting == sum(i.group == Group.WAITING for i in everything.items) == 1
    assert c.snoozed == sum(i.group == Group.SNOOZED for i in everything.items) == 1

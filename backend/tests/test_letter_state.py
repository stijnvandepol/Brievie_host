import ast
import random
from datetime import date, timedelta
from pathlib import Path

from apps.tasks import letter_state
from apps.tasks.letter_state import LetterStatus, TaskFacts, facts_of, status_of

TODAY = date(2026, 11, 10)


def _day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


def _task(
    *, open: bool = True, deadline: int | None = None, waiting: int | None = None
) -> TaskFacts:
    return TaskFacts(
        open=open,
        deadline=None if deadline is None else _day(deadline),
        waiting_until=None if waiting is None else _day(waiting),
    )


def _status(tasks: list[TaskFacts], *, reviewed: bool = True) -> tuple[LetterStatus, bool]:
    return status_of(reviewed, facts_of(tasks), TODAY)


def test_not_reviewed_is_new_whatever_the_tasks() -> None:
    assert _status([_task(deadline=-5)], reviewed=False) == (LetterStatus.NEW, False)
    assert _status([], reviewed=False) == (LetterStatus.NEW, False)


def test_reviewed_without_tasks_is_handled() -> None:
    assert _status([]) == (LetterStatus.HANDLED, False)


def test_all_done_is_handled() -> None:
    assert _status([_task(open=False, deadline=-3), _task(open=False)]) == (
        LetterStatus.HANDLED,
        False,
    )


def test_one_active_open_task_is_open() -> None:
    assert _status([_task(deadline=4)]) == (LetterStatus.OPEN, False)


def test_every_open_task_waiting_after_today_is_waiting() -> None:
    tasks = [_task(waiting=3), _task(waiting=9), _task(open=False)]
    assert _status(tasks) == (LetterStatus.WAITING, False)
    assert facts_of(tasks).wait_until == _day(3)


def test_one_waiting_one_active_is_open() -> None:
    assert _status([_task(waiting=3), _task()]) == (LetterStatus.OPEN, False)


def test_waiting_date_today_is_open_again() -> None:
    assert _status([_task(waiting=0)]) == (LetterStatus.OPEN, False)


def test_overdue_needs_deadline_before_today() -> None:
    assert _status([_task(deadline=0)]) == (LetterStatus.OPEN, False)
    assert _status([_task(deadline=-1)]) == (LetterStatus.OPEN, True)


def test_waiting_task_is_not_overdue_until_its_date() -> None:
    tasks = [TaskFacts(open=True, deadline=date(2026, 11, 1), waiting_until=date(2026, 11, 15))]
    facts = facts_of(tasks)
    assert facts.overdue_from == date(2026, 11, 15)
    assert status_of(True, facts, date(2026, 11, 14)) == (LetterStatus.WAITING, False)
    assert status_of(True, facts, date(2026, 11, 15)) == (LetterStatus.OPEN, True)


def test_overdue_from_is_deadline_plus_one_without_waiting() -> None:
    assert facts_of([_task(deadline=2)]).overdue_from == _day(3)


def test_undated_tasks_never_overdue() -> None:
    facts = facts_of([_task(), _task(waiting=-2)])
    assert facts.overdue_from is None
    assert status_of(True, facts, _day(400)) == (LetterStatus.OPEN, False)


def test_a_late_active_task_marks_a_letter_whose_other_task_waits() -> None:
    assert _status([_task(deadline=-2), _task(deadline=-9, waiting=5)]) == (
        LetterStatus.OPEN,
        True,
    )


def _direct(tasks: list[TaskFacts], today: date) -> tuple[LetterStatus, bool]:
    """S 4.1, said over the tasks themselves."""
    open_tasks = [t for t in tasks if t.open]
    if not open_tasks:
        return LetterStatus.HANDLED, False

    def active(task: TaskFacts) -> bool:
        return task.waiting_until is None or task.waiting_until <= today

    overdue = any(active(t) and t.deadline is not None and t.deadline < today for t in open_tasks)
    if any(active(t) for t in open_tasks):
        return LetterStatus.OPEN, overdue
    return LetterStatus.WAITING, overdue


def test_facts_agree_with_direct_rule() -> None:
    rng = random.Random(20261121)  # noqa: S311 (a seed, not a secret)
    for _round in range(500):
        tasks = [
            _task(
                open=rng.random() < 0.7,
                deadline=rng.choice([None, *range(-6, 7)]),
                waiting=rng.choice([None, None, *range(-6, 7)]),
            )
            for _n in range(rng.randint(0, 5))
        ]
        today = _day(rng.randint(-8, 8))
        assert status_of(True, facts_of(tasks), today) == _direct(tasks, today), tasks


def test_module_imports_no_django() -> None:
    tree = ast.parse(Path(letter_state.__file__).read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert not [name for name in names if name.startswith(("django", "apps"))]

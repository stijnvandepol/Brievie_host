"""A letter's status, as a pure rule (spec "Briefstatus" S 4.1, 4.2).

No database and no Django. A letter's tasks become three facts that do not change with
the date (`facts_of`); those facts and today become a status and the marker "Te laat"
(`status_of`). The facts are cached on the letter by `apps.tasks.letter_sync`; the status
is never stored, and nothing happens when a date passes.

The status "new" (not reviewed) is another thing than a member's "ongelezen" (unread):
that one is per user (`ReadMark`), this one is the letter's own.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final

ONE_DAY: Final = timedelta(days=1)


class LetterStatus(StrEnum):
    NEW = "new"
    OPEN = "open"
    WAITING = "waiting"
    HANDLED = "handled"


@dataclass(frozen=True)
class TaskFacts:
    open: bool
    deadline: date | None
    waiting_until: date | None


@dataclass(frozen=True)
class LetterFacts:
    # Open tasks of the letter.
    open_count: int
    # When every open task waits: the earliest of their waiting dates; else None.
    wait_until: date | None
    # The first day the letter is late; None when no open task has a deadline.
    overdue_from: date | None


NO_TASKS: Final = LetterFacts(open_count=0, wait_until=None, overdue_from=None)


def facts_of(tasks: Iterable[TaskFacts]) -> LetterFacts:
    open_tasks = [task for task in tasks if task.open]
    waits = [task.waiting_until for task in open_tasks if task.waiting_until is not None]
    every_task_waits = bool(open_tasks) and len(waits) == len(open_tasks)
    # A task is late on day D when D > deadline and it does not wait on D.
    late_from = [
        max(task.deadline + ONE_DAY, task.waiting_until)
        if task.waiting_until is not None
        else task.deadline + ONE_DAY
        for task in open_tasks
        if task.deadline is not None
    ]
    return LetterFacts(
        open_count=len(open_tasks),
        wait_until=min(waits) if every_task_waits else None,
        overdue_from=min(late_from) if late_from else None,
    )


def status_of(reviewed: bool, facts: LetterFacts, today: date) -> tuple[LetterStatus, bool]:
    """The status and whether the letter is late ("Te laat" is a marker, not a status)."""
    if not reviewed:
        return LetterStatus.NEW, False
    overdue = facts.overdue_from is not None and facts.overdue_from <= today
    if facts.open_count == 0:
        return LetterStatus.HANDLED, False
    if facts.wait_until is not None and facts.wait_until > today:
        return LetterStatus.WAITING, overdue
    return LetterStatus.OPEN, overdue

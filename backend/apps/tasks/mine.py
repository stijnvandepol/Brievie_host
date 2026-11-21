"""Whose task it is, as a pure rule (spec "Mijn taken" S 5.1, 5.2, 6).

No database and no Django: the facts come in (`MemberFacts` once per request, read by
`apps.tasks.facts` through the visibility functions; `TaskFacts` per task) and a reason
and a date group go out. `apps.tasks.services` builds the same rule as one query filter;
`test_tasks_mine.py` and the service tests keep the two equal.

The task policy decides which tasks a member sees at all; a reason only sorts what is
visible into "Mijn taken", "Op te pakken" or neither.
"""

from collections.abc import Set
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID


class Reason(StrEnum):
    ASSIGNED = "assigned"
    ADDRESSEE = "addressee"
    DEPARTMENT = "department"
    UNASSIGNED = "unassigned"
    OTHER = "other"


class Group(StrEnum):
    OVERDUE = "overdue"
    WITHIN_WEEK = "within_week"
    LATER = "later"
    NO_DATE = "no_date"


class Part(StrEnum):
    MINE = "mine"
    TO_PICK_UP = "to_pick_up"
    ALL = "all"


@dataclass(frozen=True)
class MemberFacts:
    user_id: UUID
    # Persons linked to the member (kind person).
    person_ids: frozenset[UUID]
    # The member's departments; empty while departments are off.
    department_ids: frozenset[UUID]
    # Flagged as sorter, or an admin while nobody is flagged.
    sorts_post: bool
    # Addressees somebody reads through; empty unless `sorts_post`.
    covered_ids: frozenset[UUID]
    # An organisation (`Workspace.kind == business`): "Op te pakken" is its own part.
    split: bool


@dataclass(frozen=True)
class TaskFacts:
    assigned_to_id: UUID | None
    created_by_id: UUID | None
    has_letter: bool
    addressee_ids: frozenset[UUID]


def reason_for(task: TaskFacts, member: MemberFacts) -> Reason:
    """S 5.1: one reason per visible task. An assignee wins; otherwise the letter's
    addressees decide (own person, own department, unnamed post for a sorter). A task
    without a letter belongs to its assignee, else to its maker."""
    if task.assigned_to_id is not None:
        return Reason.ASSIGNED if task.assigned_to_id == member.user_id else Reason.OTHER
    if not task.has_letter:
        return Reason.ADDRESSEE if task.created_by_id == member.user_id else Reason.OTHER
    if _meets(task.addressee_ids, member.person_ids):
        return Reason.ADDRESSEE
    if _meets(task.addressee_ids, member.department_ids):
        return Reason.DEPARTMENT
    if member.sorts_post and not _meets(task.addressee_ids, member.covered_ids):
        return Reason.UNASSIGNED
    return Reason.OTHER


def _meets(a: Set[UUID], b: Set[UUID]) -> bool:
    return not a.isdisjoint(b)


_EVERY: Final = frozenset(Reason)

# (split, part) -> the reasons in that part (S 5.2). At home one list holds everything
# that is the member's; an organisation keeps department and unnamed post apart.
PARTS: Final[dict[tuple[bool, str], frozenset[Reason]]] = {
    (False, Part.MINE): frozenset(
        {Reason.ASSIGNED, Reason.ADDRESSEE, Reason.DEPARTMENT, Reason.UNASSIGNED}
    ),
    (False, Part.TO_PICK_UP): frozenset(),
    (False, Part.ALL): _EVERY,
    (True, Part.MINE): frozenset({Reason.ASSIGNED, Reason.ADDRESSEE}),
    (True, Part.TO_PICK_UP): frozenset({Reason.DEPARTMENT, Reason.UNASSIGNED}),
    (True, Part.ALL): _EVERY,
}

# "Binnen 7 dagen": today up to and including today + 6 (S 6; no weekend cliff, MT9).
WEEK: Final = timedelta(days=6)


def group_for(deadline: date | None, today: date) -> Group:
    if deadline is None:
        return Group.NO_DATE
    if deadline < today:
        return Group.OVERDUE
    if deadline <= today + WEEK:
        return Group.WITHIN_WEEK
    return Group.LATER


def days_left(deadline: date | None, today: date) -> int | None:
    """Days until the deadline: 0 today, negative when overdue, None without a date."""
    return None if deadline is None else (deadline - today).days

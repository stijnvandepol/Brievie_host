"""Whose task it is, the pure rule (spec S 5.1, 5.2, 6; plan Task 4). No database."""

import ast
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from apps.tasks.mine import (
    PARTS,
    Group,
    MemberFacts,
    Part,
    Reason,
    TaskFacts,
    days_left,
    group_for,
    reason_for,
)

ME = uuid4()
SOMEONE = uuid4()
MY_PERSON = uuid4()
PIET = uuid4()
FINANCE = uuid4()
JAN = uuid4()  # a person without an account: not covered


def member(
    *,
    departments: bool = True,
    sorts: bool = False,
    covered: frozenset[UUID] = frozenset({MY_PERSON, PIET, FINANCE}),
) -> MemberFacts:
    return MemberFacts(
        user_id=ME,
        person_ids=frozenset({MY_PERSON}),
        department_ids=frozenset({FINANCE}) if departments else frozenset(),
        sorts_post=sorts,
        covered_ids=covered if sorts else frozenset(),
        split=False,
    )


def on(*addressees: UUID, assigned_to: UUID | None = None) -> TaskFacts:
    return TaskFacts(
        assigned_to_id=assigned_to,
        created_by_id=None,
        has_letter=True,
        addressee_ids=frozenset(addressees),
    )


def letterless(*, assigned_to: UUID | None = None, created_by: UUID | None = None) -> TaskFacts:
    return TaskFacts(
        assigned_to_id=assigned_to,
        created_by_id=created_by,
        has_letter=False,
        addressee_ids=frozenset(),
    )


@pytest.mark.parametrize(
    ("task", "facts", "expected"),
    [
        (on(PIET, assigned_to=ME), member(), Reason.ASSIGNED),
        (on(MY_PERSON, assigned_to=SOMEONE), member(), Reason.OTHER),
        (on(MY_PERSON), member(), Reason.ADDRESSEE),
        (on(MY_PERSON, PIET), member(), Reason.ADDRESSEE),
        (on(FINANCE), member(), Reason.DEPARTMENT),
        (on(FINANCE), member(departments=False), Reason.OTHER),
        (on(MY_PERSON, FINANCE), member(), Reason.ADDRESSEE),
        (on(), member(sorts=True), Reason.UNASSIGNED),
        (on(JAN), member(sorts=True), Reason.UNASSIGNED),
        (on(PIET), member(sorts=True), Reason.OTHER),
        (on(PIET, JAN), member(sorts=True), Reason.OTHER),
        (on(), member(sorts=False), Reason.OTHER),
        (on(PIET), member(), Reason.OTHER),
        (letterless(created_by=ME), member(), Reason.ADDRESSEE),
        (letterless(created_by=SOMEONE, assigned_to=ME), member(), Reason.ASSIGNED),
        (letterless(created_by=ME, assigned_to=SOMEONE), member(), Reason.OTHER),
        (letterless(), member(sorts=True), Reason.OTHER),
    ],
)
def test_reason_for(task: TaskFacts, facts: MemberFacts, expected: Reason) -> None:
    assert reason_for(task, facts) is expected


def test_parts_at_home_hold_everything_that_is_mine() -> None:
    assert PARTS[(False, Part.MINE)] == {
        Reason.ASSIGNED,
        Reason.ADDRESSEE,
        Reason.DEPARTMENT,
        Reason.UNASSIGNED,
    }
    assert PARTS[(False, Part.TO_PICK_UP)] == frozenset()
    assert PARTS[(False, Part.ALL)] == frozenset(Reason)


def test_parts_in_an_organisation_split_shared_work() -> None:
    assert PARTS[(True, Part.MINE)] == {Reason.ASSIGNED, Reason.ADDRESSEE}
    assert PARTS[(True, Part.TO_PICK_UP)] == {Reason.DEPARTMENT, Reason.UNASSIGNED}
    assert PARTS[(True, Part.ALL)] == frozenset(Reason)
    # Mine and to pick up never overlap, and neither holds someone else's task.
    for split in (False, True):
        assert not PARTS[(split, Part.MINE)] & PARTS[(split, Part.TO_PICK_UP)]
        assert Reason.OTHER not in PARTS[(split, Part.MINE)] | PARTS[(split, Part.TO_PICK_UP)]


TODAY = date(2026, 11, 21)


@pytest.mark.parametrize(
    ("deadline", "group", "left"),
    [
        (date(2026, 11, 20), Group.OVERDUE, -1),
        (date(2026, 11, 21), Group.WITHIN_WEEK, 0),
        (date(2026, 11, 27), Group.WITHIN_WEEK, 6),
        (date(2026, 11, 28), Group.LATER, 7),
        (None, Group.NO_DATE, None),
    ],
)
def test_group_and_days_left(deadline: date | None, group: Group, left: int | None) -> None:
    assert group_for(deadline, TODAY) is group
    assert days_left(deadline, TODAY) == left


def test_the_rule_has_no_database() -> None:
    import apps.tasks.mine as mine

    tree = ast.parse(Path(mine.__file__).read_text(encoding="utf-8"))
    imported = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for name in (
            [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else [a.name for a in node.names]
        )
    }
    assert not any(name.split(".")[0] in {"django", "apps"} for name in imported), imported

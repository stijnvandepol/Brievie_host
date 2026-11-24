"""documents/0024 fills the cached task facts of letters that already have tasks: back to
0023 (the columns go), forward again, and every letter has the facts the pure rule gives
(plan Task 2, "backfill matches the pure rule")."""

from collections.abc import Iterator
from datetime import date

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.documents.models import Document
from apps.tasks.letter_state import TaskFacts, facts_of
from apps.workspaces.context import SYSTEM
from tests.letter_status_support import T
from tests.letter_status_support import letter_with_tasks as make
from tests.my_tasks_support import household
from tests.visibility_support import as_actor


def _migrate(targets: list[tuple[str, str]]) -> None:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)


def _leaves() -> list[tuple[str, str]]:
    executor = MigrationExecutor(connection)
    leaves: list[tuple[str, str]] = executor.loader.graph.leaf_nodes()
    return leaves


@pytest.fixture
def migrated_forward_afterwards() -> Iterator[None]:
    yield
    _migrate(_leaves())


@pytest.mark.django_db(transaction=True)
def test_backfill_matches_the_pure_rule(migrated_forward_afterwards: None) -> None:
    h = household()
    # (open, deadline) per task; waiting days do not survive going back (the column goes).
    rows: dict[str, list[tuple[bool, date | None]]] = {
        "none": [],
        "all done": [(False, date(2026, 1, 1))],
        "open undated": [(True, None)],
        "open and late": [(True, date(2026, 1, 1)), (False, None), (True, None)],
        "two dated": [(True, date(2026, 5, 1)), (True, date(2026, 3, 1))],
    }
    made = {
        name: make(
            h.workspace,
            [T(status="open" if open_ else "done", deadline=deadline) for open_, deadline in tasks],
        )[0]
        for name, tasks in rows.items()
    }
    expected = {
        made[name].id: facts_of(TaskFacts(open_, deadline, None) for open_, deadline in tasks)
        for name, tasks in rows.items()
    }
    with as_actor(h.workspace, SYSTEM):
        # Wrong on purpose: the migration must not trust what stands there.
        Document.objects.filter(id__in=expected).update(task_open_count=9)

    _migrate([("documents", "0023_read_mark_opened"), ("tasks", "0005_my_tasks")])
    _migrate(_leaves())

    with as_actor(h.workspace, SYSTEM):
        stored = {
            d.id: (d.task_open_count, d.task_wait_until, d.task_overdue_from)
            for d in Document.objects.filter(id__in=expected)
        }
    assert stored == {
        i: (facts.open_count, facts.wait_until, facts.overdue_from) for i, facts in expected.items()
    }

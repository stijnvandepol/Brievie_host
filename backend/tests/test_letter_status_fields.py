"""Letter status storage (plan Task 2): the three task facts on the letter, the partial
index, the new event kinds and `on_date`, `Task.waiting_until` and `TaskSnooze` under forced
row-level security."""

import re
from datetime import date
from pathlib import Path

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from apps.documents.models import DocumentEvent
from apps.tasks import letter_sync
from apps.tasks.models import TaskSnooze
from apps.workspaces.context import SYSTEM
from apps.workspaces.rls import TASK_SNOOZE_POLICY, VISIBILITY_POLICIES
from tests.letter_status_support import T, letter_with_tasks
from tests.my_tasks_support import household
from tests.visibility_support import as_actor

TABLE = "tasks_tasksnooze"


def test_document_task_facts_default_empty() -> None:
    h = household()
    document, _ = letter_with_tasks(h.workspace)
    assert document.task_open_count == 0
    assert document.task_wait_until is None
    assert document.task_overdue_from is None


def test_open_tasks_index_exists() -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'documents_document_open_tasks'"
        )
        row = c.fetchone()
    assert row is not None
    where = row[0].split("WHERE", 1)[1]
    assert "task_open_count" in where
    assert "'done'" in where


def test_the_open_count_has_a_database_default() -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'documents_document' AND column_name = 'task_open_count'"
        )
        row = c.fetchone()
    assert row == ("0",)


def test_new_event_kinds_and_on_date() -> None:
    h = household()
    document, _ = letter_with_tasks(h.workspace)
    with as_actor(h.workspace, SYSTEM):
        for kind in ("task_waiting", "task_waiting_ended", "letter_handled", "letter_reopened"):
            event = DocumentEvent.objects.create(
                workspace=h.workspace,
                document=document,
                kind=kind,
                origin="system",
                on_date=date(2026, 12, 3),
            )
            event.refresh_from_db()
            assert event.on_date == date(2026, 12, 3)
        assert DocumentEvent.objects.filter(document=document, on_date__isnull=False).count() == 4


def test_task_waiting_until_defaults_null() -> None:
    h = household()
    _, tasks = letter_with_tasks(h.workspace, [T()])
    assert tasks[0].waiting_until is None


def test_tasksnooze_rls_enabled_and_forced() -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            [TABLE],
        )
        assert c.fetchone() == (True, True)
        c.execute("SELECT polname FROM pg_policy WHERE polrelid = %s::regclass", [TABLE])
        names = {row[0] for row in c.fetchall()}
    assert {TASK_SNOOZE_POLICY, "workspace_isolation", *VISIBILITY_POLICIES} <= names


def test_tasksnooze_unique_per_task_and_user() -> None:
    h = household()
    _, tasks = letter_with_tasks(h.workspace, [T()])
    with as_actor(h.workspace, SYSTEM):
        TaskSnooze.objects.create(
            workspace=h.workspace, task=tasks[0], user=h.anne, until=date(2026, 12, 1)
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            TaskSnooze.objects.create(
                workspace=h.workspace, task=tasks[0], user=h.anne, until=date(2026, 12, 2)
            )
        TaskSnooze.objects.create(
            workspace=h.workspace, task=tasks[0], user=h.piet, until=date(2026, 12, 2)
        )


def test_tasksnooze_rows_are_own_only() -> None:
    h = household()
    _, tasks = letter_with_tasks(h.workspace, [T()])
    with as_actor(h.workspace, SYSTEM):
        row = TaskSnooze.objects.create(
            workspace=h.workspace, task=tasks[0], user=h.anne, until=date(2026, 12, 1)
        )
    with as_actor(h.workspace, h.piet.id):
        assert TaskSnooze.objects.count() == 0
        assert TaskSnooze.objects.filter(id=row.id).delete()[0] == 0
        assert TaskSnooze.objects.filter(id=row.id).update(until=date(2027, 1, 1)) == 0
        with pytest.raises(DatabaseError), transaction.atomic():
            TaskSnooze.objects.create(
                workspace=h.workspace, task=tasks[0], user=h.anne, until=date(2026, 12, 3)
            )
    with as_actor(h.workspace, h.anne.id):
        assert TaskSnooze.objects.count() == 1


def test_the_backfill_statement_is_the_one_the_repair_uses() -> None:
    # The newest backfill: documents 0029 made the facts a letter's only (archive AR7).
    migration = (
        Path(__file__).parents[1] / "apps/documents/migrations/0030_letter_facts_letters_only.py"
    ).read_text()
    match = re.search(r'RECOMPUTE: Final = """(.*?)"""', migration, re.DOTALL)
    assert match is not None
    assert match.group(1) == letter_sync.RECOMPUTE


def test_the_repair_matches_the_pure_rule() -> None:
    h = household()
    document, _ = letter_with_tasks(
        h.workspace,
        [
            T(deadline=date(2026, 11, 1), waiting_until=date(2026, 11, 15)),
            T(deadline=date(2026, 12, 1), waiting_until=date(2026, 11, 18)),
            T(status="done", deadline=date(2026, 10, 1)),
        ],
    )
    assert document.task_open_count == 2
    assert document.task_wait_until == date(2026, 11, 15)
    assert document.task_overdue_from == date(2026, 11, 15)

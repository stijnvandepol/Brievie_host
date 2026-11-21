"""The fields "Mijn taken" adds to a task (spec S 12, plan Task 1)."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction

from apps.documents.models import DocumentEvent
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace


def _task(workspace: Workspace, **fields: object) -> Task:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Task.objects.create(
            workspace=workspace, type="pay", description="Betaal de rekening", **fields
        )


def test_task_amount_is_decimal_and_not_negative(ws_a: Workspace) -> None:
    task = _task(ws_a, amount=Decimal("12.50"))
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Task.objects.get(id=task.id).amount == Decimal("12.50")
        with pytest.raises(IntegrityError), transaction.atomic():
            Task.objects.create(workspace=ws_a, type="pay", description="Min", amount=-1)


def test_task_new_fields_default_empty(ws_a: Workspace) -> None:
    task = _task(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        stored = Task.objects.get(id=task.id)
    assert stored.amount is None
    assert stored.term_quote == ""
    assert stored.completed_by is None
    assert stored.assigned_by is None


def test_term_quote_has_a_database_default(ws_a: Workspace) -> None:
    """A raw insert that leaves the new column out (the performance seed) still works."""
    with workspace_context(ws_a.id, actor=SYSTEM), connection.cursor() as c:
        c.execute(
            "INSERT INTO tasks_task (id, created_at, updated_at, workspace_id, type,"
            " description, deadline_source, deadline_quote, status, created_by_ai)"
            " VALUES (gen_random_uuid(), now(), now(), %s, 'pay', 'Rauw', 'manual', '',"
            " 'open', false) RETURNING term_quote, amount",
            [ws_a.id],
        )
        assert c.fetchone() == ("", None)


def test_open_task_index_exists() -> None:
    with connection.cursor() as c:
        c.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'tasks_task_open'")
        row = c.fetchone()
    assert row is not None
    assert "WHERE" in row[0]
    assert "'open'" in row[0]


def test_task_event_kinds_exist() -> None:
    assert {"task_assigned", "task_unassigned", "task_deadline_set"} <= set(
        DocumentEvent.Kind.values
    )
    assert "system" in DocumentEvent.Origin.values

"""The one writer of a letter's task facts (plan Task 3, spec S 4.2, 4.3): every task change
keeps the facts right, the status lines are written only when the letter's handled-ness
changes, the lock order is letter then task, the nightly repair finds nothing after any
sequence of operations and fixes a tampered row."""

import random
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.documents import review
from apps.documents import services as document_services
from apps.documents.models import Document
from apps.documents.schemas import ConfirmIn, TaskIn
from apps.tasks import letter_sync, services
from apps.tasks.letter_state import LetterFacts, TaskFacts, facts_of
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from tests.documents_support import pair, trashed
from tests.letter_status_support import T, kinds, letter_with_tasks, stored
from tests.my_tasks_support import Household, household
from tests.test_duplicates_merge import do_merge

pytestmark = pytest.mark.usefixtures("without_preset_rules")

APP = Path(__file__).parents[1] / "apps"
D1 = date(2026, 11, 1)


def test_sync_writes_facts_after_complete() -> None:
    h = household()
    document, (first, _second) = letter_with_tasks(h.workspace, [T(deadline=D1), T()])
    assert stored(document.id, h.workspace) == LetterFacts(2, None, D1 + timedelta(days=1))
    with workspace_context(h.workspace.id, actor=h.anne.id):
        services.complete_task(workspace=h.workspace, user=h.anne, task_id=first.id)
    assert stored(document.id, h.workspace) == LetterFacts(1, None, None)
    assert kinds(document.id, h.workspace) == ["task_completed"]


def test_last_task_completed_writes_system_handled_line() -> None:
    h = household()
    document, (only,) = letter_with_tasks(h.workspace, [T()])
    with workspace_context(h.workspace.id, actor=h.anne.id):
        services.complete_task(workspace=h.workspace, user=h.anne, task_id=only.id)
    assert kinds(document.id, h.workspace) == ["task_completed", "letter_handled"]
    from apps.documents.models import DocumentEvent

    with workspace_context(h.workspace.id, actor=SYSTEM):
        line = DocumentEvent.objects.get(document=document, kind="letter_handled")
    assert line.origin == "system"
    assert line.actor_id is None


def test_reopen_on_handled_letter_writes_system_reopened_line() -> None:
    h = household()
    document, (only,) = letter_with_tasks(h.workspace, [T()])
    with workspace_context(h.workspace.id, actor=h.anne.id):
        services.complete_task(workspace=h.workspace, user=h.anne, task_id=only.id)
        services.reopen_task(workspace=h.workspace, user=h.anne, task_id=only.id)
    assert kinds(document.id, h.workspace) == [
        "task_completed",
        "letter_handled",
        "task_reopened",
        "letter_reopened",
    ]
    assert stored(document.id, h.workspace).open_count == 1


def _confirm(h: Household, document: Document, tasks: list[TaskIn]) -> None:
    data = ConfirmIn(
        sender="Afzender",
        document_type="",
        summary="Samenvatting",
        summary_by_ai=False,
        tasks=tasks,
    )
    with workspace_context(h.workspace.id, actor=h.anne.id):
        review.confirm_document(
            workspace=h.workspace, user=h.anne, document_id=document.id, data=data
        )


def test_confirm_without_tasks_is_handled_with_line() -> None:
    h = household()
    document, _ = letter_with_tasks(h.workspace, status="needs_review")
    _confirm(h, document, [])
    assert stored(document.id, h.workspace).open_count == 0
    assert kinds(document.id, h.workspace) == ["confirmed", "letter_handled"]


def test_confirm_with_tasks_writes_no_status_line() -> None:
    h = household()
    document, _ = letter_with_tasks(h.workspace, status="needs_review")
    task = TaskIn(
        type="pay",
        description="Betaal de rekening",
        deadline=date(2099, 1, 1),
        deadline_source="manual",
    )
    _confirm(h, document, [task])
    assert stored(document.id, h.workspace) == LetterFacts(1, None, date(2099, 1, 2))
    assert kinds(document.id, h.workspace) == ["confirmed"]


def test_no_line_for_trashed_or_unreviewed_letter() -> None:
    h = household()
    gone, (task,) = letter_with_tasks(h.workspace, [T()])
    trashed(gone)
    with workspace_context(h.workspace.id, actor=SYSTEM):
        Task.objects.filter(id=task.id).update(status="done")
        letter_sync.sync(Document.objects.get(id=gone.id))
    assert stored(gone.id, h.workspace).open_count == 0
    assert kinds(gone.id, h.workspace) == []
    unchecked, (open_task,) = letter_with_tasks(h.workspace, [T()], status="needs_review")
    with workspace_context(h.workspace.id, actor=SYSTEM):
        Task.objects.filter(id=open_task.id).update(status="done")
        letter_sync.sync(Document.objects.get(id=unchecked.id))
    assert kinds(unchecked.id, h.workspace) == []


def test_merge_moving_open_task_reopens_kept_letter() -> None:
    h = household()
    keep, _ = letter_with_tasks(h.workspace, [T(status="done", description="Gedaan")])
    other, _ = letter_with_tasks(h.workspace, [T(type="pay", description="Betalen")])
    candidate = pair(keep, other)
    do_merge(h.workspace, candidate, keep, user=h.anne)
    assert stored(keep.id, h.workspace).open_count == 1
    assert stored(other.id, h.workspace).open_count == 0
    assert "letter_reopened" in kinds(keep.id, h.workspace)
    assert "letter_handled" not in kinds(other.id, h.workspace)


def test_merge_that_completes_a_twin_and_moves_an_open_task_leaves_the_letter_open() -> None:
    """The order of the review of PR #50: the twin's completion syncs its own instance of the
    kept letter ("handled", one line), then the moved open task arrives; the final sync must
    compare with the database, not with the instance the merge loaded before."""
    h = household()
    keep, (twin,) = letter_with_tasks(h.workspace, [T(type="pay", description="Betalen")])
    other, _ = letter_with_tasks(
        h.workspace,
        [T(type="pay", status="done", description="Betaald"), T(description="Bel terug")],
    )
    do_merge(h.workspace, pair(keep, other), keep, user=h.anne)
    assert stored(keep.id, h.workspace).open_count == 1
    lines = kinds(keep.id, h.workspace)
    assert lines.count("letter_handled") == 1
    assert lines.index("letter_reopened") > lines.index("letter_handled")
    assert twin.id


def test_a_stale_instance_does_not_hide_a_change_from_sync() -> None:
    h = household()
    document, (task,) = letter_with_tasks(h.workspace, [T()])
    with workspace_context(h.workspace.id, actor=SYSTEM):
        stale = Document.objects.get(id=document.id)
        Task.objects.filter(id=task.id).update(status="done")
        letter_sync.sync(Document.objects.get(id=document.id))
        Task.objects.filter(id=task.id).update(status="open")
        letter_sync.sync(stale)
    assert stored(document.id, h.workspace).open_count == 1
    assert kinds(document.id, h.workspace) == ["letter_handled", "letter_reopened"]


def test_reopen_document_clears_ai_tasks_facts() -> None:
    h = household()
    document, (ai_task,) = letter_with_tasks(h.workspace, [T()])
    with workspace_context(h.workspace.id, actor=SYSTEM):
        Task.objects.filter(id=ai_task.id).update(created_by_ai=True)
        Document.objects.filter(id=document.id).update(confirmed_by_ai=True)
    with workspace_context(h.workspace.id, actor=h.anne.id):
        document_services.reopen_document(
            workspace=h.workspace, user=h.anne, document_id=document.id
        )
    assert stored(document.id, h.workspace).open_count == 0
    assert kinds(document.id, h.workspace) == ["reopened"]


def test_only_letter_sync_writes_task_facts() -> None:
    allowed = {"tasks/letter_sync.py"}
    # An assignment to the attribute, or a keyword of `update(...)`; not a lookup in `Q(...)`.
    pattern = re.compile(
        r"\.task_(open_count|wait_until|overdue_from)\s*=[^=]"
        r"|update\([^)]*\btask_(open_count|wait_until|overdue_from)="
    )
    found = []
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        if "migrations/" in relative or relative in allowed or relative == "documents/models.py":
            continue
        if pattern.search(path.read_text()):
            found.append(relative)
    assert found == []


def test_every_task_writer_syncs() -> None:
    wanted = {
        "tasks/services.py": 4,  # complete_task, reopen_task, set_deadline (via _sync)
        "documents/review.py": 1,
        "documents/services.py": 1,
        "documents/duplicates.py": 2,
    }
    for relative, at_least in wanted.items():
        text = (APP / relative).read_text()
        calls = len(re.findall(r"letter_sync\.sync\(|\bsync_letter\(", text))
        assert calls >= at_least, relative


def test_task_change_locks_letter_before_task() -> None:
    h = household()
    _, (task, _other) = letter_with_tasks(h.workspace, [T(), T()])
    with (
        workspace_context(h.workspace.id, actor=h.anne.id),
        CaptureQueriesContext(connection) as queries,
    ):
        services.complete_task(workspace=h.workspace, user=h.anne, task_id=task.id)
    locking = [
        q["sql"] for q in queries if "FOR NO KEY UPDATE" in q["sql"] or "FOR UPDATE" in q["sql"]
    ]
    assert "documents_document" in locking[0]
    assert "FOR NO KEY UPDATE" in locking[0]
    assert "tasks_task" in locking[1]


def test_random_operations_leave_nothing_to_repair() -> None:
    h = household()
    rng = random.Random(20261121)  # noqa: S311 - a seeded test, not a secret
    letters = [letter_with_tasks(h.workspace, [T(deadline=D1) for _ in range(3)]) for _ in range(4)]
    tasks = [t for _, made in letters for t in made]
    with workspace_context(h.workspace.id, actor=h.anne.id):
        for _ in range(300):
            task = rng.choice(tasks)
            action = rng.choice(["complete", "reopen", "date", "nodate"])
            if action == "complete":
                services.complete_task(workspace=h.workspace, user=h.anne, task_id=task.id)
            elif action == "reopen":
                services.reopen_task(workspace=h.workspace, user=h.anne, task_id=task.id)
            else:
                day = date(2026, 11, 1) + timedelta(days=rng.randrange(60))
                services.set_deadline(
                    workspace=h.workspace,
                    user=h.anne,
                    task_id=task.id,
                    deadline=day if action == "date" else None,
                )
    assert letter_sync.verify_all() == 0


def test_verify_all_repairs_a_tampered_row() -> None:
    h = household()
    document, _ = letter_with_tasks(h.workspace, [T(), T()])
    with workspace_context(h.workspace.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(task_open_count=7)
    assert letter_sync.verify_all() == 1
    assert stored(document.id, h.workspace).open_count == 2
    assert letter_sync.verify_all() == 0


def test_stored_facts_match_the_pure_rule_after_the_repair() -> None:
    h = household()
    rows = [T(deadline=D1, waiting_until=date(2026, 11, 15)), T(waiting_until=date(2026, 11, 3))]
    document, _ = letter_with_tasks(h.workspace, rows)
    expected = facts_of(TaskFacts(True, t.deadline, t.waiting_until) for t in rows)
    assert stored(document.id, h.workspace) == expected

"""The one writer of a letter's task facts (spec "Briefstatus" S 4.2, 4.3).

`Document.task_open_count`, `task_wait_until` and `task_overdue_from` are derived from the
letter's tasks (`apps.tasks.letter_state.facts_of`). `sync` writes them in the same
transaction as every task change, under the letter's row lock (letter first, then task);
`verify_all` recomputes them nightly and repairs. Nothing else assigns them (a source test
checks it), so nobody can make a letter "Afgehandeld" while a task is open.

Both run as the system: the caller was authorised first (`require_editable`), and a
confirm that hands the letter away must still write them. Neither returns anything about
the letter to the caller, and neither changes who reads it.
"""

from typing import Final
from uuid import UUID

from django.db import connection

from apps.accounts.models import User
from apps.documents import organise
from apps.documents.models import Document
from apps.tasks.letter_state import NO_TASKS, LetterFacts, TaskFacts, facts_of
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, system_actor, workspace_context
from apps.workspaces.models import Workspace

# Per letter of the workspace: the open tasks counted, the earliest waiting date when every
# open task waits, and the first day one is late (GREATEST skips a missing waiting date).
# The same statement fills the columns in documents/0024. An own document has no letter
# facts (archive spec AR7): its tasks (the expiry reminder) are not joined, so it stays at
# zero open tasks and no dates.
RECOMPUTE: Final = """
UPDATE documents_document d
SET task_open_count = f.open_count,
    task_wait_until = f.wait_until,
    task_overdue_from = f.overdue_from
FROM (
    SELECT l.id,
           COALESCE(a.open_count, 0)::integer AS open_count,
           a.wait_until,
           a.overdue_from
    FROM documents_document l
    LEFT JOIN (
        SELECT t.document_id,
               count(*) AS open_count,
               CASE WHEN count(*) = count(t.waiting_until) THEN min(t.waiting_until) END
                   AS wait_until,
               min(GREATEST(t.deadline + 1, t.waiting_until))
                   FILTER (WHERE t.deadline IS NOT NULL) AS overdue_from
        FROM tasks_task t
        WHERE t.workspace_id = %(workspace)s AND t.status = 'open'
          AND t.document_id IS NOT NULL
        GROUP BY t.document_id
    ) a ON a.document_id = l.id AND l.kind = 'letter'
    WHERE l.workspace_id = %(workspace)s
) f
WHERE d.id = f.id AND d.workspace_id = %(workspace)s
  AND (d.task_open_count, d.task_wait_until, d.task_overdue_from)
      IS DISTINCT FROM (f.open_count, f.wait_until, f.overdue_from)
"""


def stored_facts(document: Document) -> LetterFacts:
    return LetterFacts(
        open_count=document.task_open_count,
        wait_until=document.task_wait_until,
        overdue_from=document.task_overdue_from,
    )


def sync(
    document: Document,
    *,
    actor: User | None = None,
    previous_status: str | None = None,
    silent: bool = False,
) -> None:
    """Bring the letter's task facts in line with its tasks; the caller holds the letter's
    row lock and made its task change in this transaction.

    Writes the history line when the letter became handled or open again (S 4.3, LS8):
    only for a reviewed letter outside the trash. `previous_status` is the letter's status
    before the caller changed it (a confirm passes the status it reviewed, so a confirm
    without tasks writes "handled"; taking a confirmation back writes nothing here). With
    `actor` ("Brief afhandelen") the handled line is that person's; otherwise the line has
    origin `system` and no actor: nobody handled the letter, its last task was ticked.
    `silent` writes no line (the letter a merge is about to put in the trash).
    """
    done = Document.Status.DONE
    with system_actor():
        # The stored facts are read from the database, under the caller's lock, never from
        # the instance: another instance of the same letter (a task completion fetches its
        # own) may have synced since this one was loaded (security review of PR #50).
        stored = (
            Document.objects.filter(id=document.id)
            .values_list("task_open_count", "task_wait_until", "task_overdue_from")
            .first()
        )
        old = stored_facts(document) if stored is None else LetterFacts(*stored)
        # An own document has no letter facts, also with an open reminder (archive AR7).
        new = (
            facts_of(
                TaskFacts(open=status == Task.Status.OPEN, deadline=deadline, waiting_until=waiting)
                for status, deadline, waiting in Task.objects.filter(document=document).values_list(
                    "status", "deadline", "waiting_until"
                )
            )
            if document.kind == Document.Kind.LETTER
            else NO_TASKS
        )
        if new != old:
            Document.objects.filter(id=document.id).update(
                task_open_count=new.open_count,
                task_wait_until=new.wait_until,
                task_overdue_from=new.overdue_from,
            )
        document.task_open_count = new.open_count
        document.task_wait_until = new.wait_until
        document.task_overdue_from = new.overdue_from
        if (
            silent
            or document.trashed_at is not None
            or document.status != done
            or document.kind != Document.Kind.LETTER
        ):
            return
        handled_before = (previous_status or document.status) == done and old.open_count == 0
        handled_after = new.open_count == 0
        if handled_after and not handled_before:
            organise.record_letter_handled(document, actor=actor)
        elif handled_before and not handled_after:
            organise.record_letter_reopened(document)


def verify_workspace(workspace_id: UUID) -> int:
    """Recompute the facts of every letter of the workspace and repair what differs; the
    number repaired. Needs the workspace's context as the system. No history line: a
    repair is not an event."""
    with connection.cursor() as cursor:
        cursor.execute(RECOMPUTE, {"workspace": workspace_id})
        return int(cursor.rowcount)


def verify_all() -> int:
    """`verify_workspace` for every workspace, each in its own short transaction."""
    fixed = 0
    for workspace_id in Workspace.objects.order_by("id").values_list("id", flat=True):
        with workspace_context(workspace_id, actor=SYSTEM):
            fixed += verify_workspace(workspace_id)
    return fixed

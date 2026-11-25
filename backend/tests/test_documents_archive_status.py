"""Status on Archief (archive plan Task 13, spec S 5, AR7, AR12): the letter status filters
the archive's letters; an own document has none, also with an open reminder, and the
counts and the nightly repair leave it out."""

import uuid
from datetime import timedelta

from django.utils import timezone

from apps.documents import reminders, services
from apps.documents.models import Document
from apps.documents.services import ArchiveFilters
from apps.tasks import letter_sync
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.archive_support import membership_of, own_document
from tests.my_tasks_support import Household, household
from tests.visibility_support import as_actor, letter


def _archive(h: Household, status: str) -> set[uuid.UUID]:
    member = membership_of(h.workspace, h.anne)
    with as_actor(h.workspace, h.anne.id):
        return {
            d.id
            for d in services.list_documents(
                member, letter_status=status, archive=ArchiveFilters(archived=True)
            )
        }


def _with_reminder(h: Household) -> Document:
    document = own_document(
        h.workspace,
        on=[h.persons["Anne"]],
        scanned_by=h.anne,
        expires_on=timezone.localdate() + timedelta(days=5),
        remind_days=30,
    )
    reminders.start_expiry_reminders()
    with as_actor(h.workspace, SYSTEM):
        assert Task.objects.filter(document=document, status="open").exists()
        return Document.objects.get(id=document.id)


def test_document_has_no_letter_status_even_with_an_open_reminder() -> None:
    h = household()
    document = _with_reminder(h)
    member = membership_of(h.workspace, h.anne)
    with as_actor(h.workspace, h.anne.id):
        [row] = services.document_summaries_out(member, [document])
    assert (row.letter_status, row.overdue, row.open_task_count) == ("", False, 0)
    assert row.reminder_task_id is not None


def test_archived_with_letter_status_handled_lists_only_handled_letters() -> None:
    h = household()
    handled = letter(h.workspace, on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        Task.objects.filter(document=handled).update(status="done")
        letter_sync.sync(Document.objects.get(id=handled.id))
    still_open = letter(h.workspace, on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        letter_sync.sync(Document.objects.get(id=still_open.id))
    document = _with_reminder(h)
    assert _archive(h, "handled") == {handled.id}
    assert _archive(h, "open") == {still_open.id}
    assert document.id not in _archive(h, "handled") | _archive(h, "open")


def test_status_counts_ignore_documents() -> None:
    h = household()
    _with_reminder(h)
    own_document(h.workspace, scanned_by=h.anne)
    member = membership_of(h.workspace, h.anne)
    with as_actor(h.workspace, h.anne.id):
        counts = services.status_counts(member)
    assert (counts.new, counts.open, counts.waiting, counts.handled, counts.overdue) == (
        0,
        0,
        0,
        0,
        0,
    )


def test_repair_skips_documents() -> None:
    h = household()
    document = _with_reminder(h)
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=document.id).update(task_open_count=3)
        assert letter_sync.verify_workspace(h.workspace.id) == 1
        stored = Document.objects.get(id=document.id)
        assert letter_sync.verify_workspace(h.workspace.id) == 0
    assert (stored.task_open_count, stored.task_overdue_from) == (0, None)

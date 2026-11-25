"""The end-date reminder among the task services (security review of PR #54, points 4 and
6): reopening an old reminder while a new one is open answers 409, not 500, and its
deadline is the document's end date, never set by hand."""

from datetime import date, timedelta

import pytest

from apps.common.errors import ApiError
from apps.documents import reminders
from apps.documents.models import Document
from apps.tasks import services
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.archive_support import own_document
from tests.my_tasks_support import household
from tests.visibility_support import as_actor

TODAY = date(2026, 11, 25)
END = TODAY + timedelta(days=20)


def _reminders(document: Document) -> list[Task]:
    with as_actor(document.workspace, SYSTEM):
        return list(Task.objects.filter(document=document, type="expiry").order_by("created_at"))


def test_reopening_an_old_reminder_while_a_new_one_is_open_is_409() -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, expires_on=END, remind_days=30)
    reminders.start_expiry_reminders(today=TODAY)
    [old] = _reminders(document)
    with as_actor(h.workspace, h.anne.id):
        services.complete_task(workspace=h.workspace, user=h.anne, task_id=old.id)
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=document.id).update(expires_on=END + timedelta(days=5))
    reminders.start_expiry_reminders(today=TODAY)
    assert [t.status for t in _reminders(document)] == ["done", "open"]
    with as_actor(h.workspace, h.anne.id), pytest.raises(ApiError) as caught:
        services.reopen_task(workspace=h.workspace, user=h.anne, task_id=old.id)
    assert (caught.value.status, caught.value.code) == (409, "reminder_already_open")
    assert [t.status for t in _reminders(document)] == ["done", "open"]


def test_reopening_the_only_reminder_works() -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, expires_on=END, remind_days=30)
    reminders.start_expiry_reminders(today=TODAY)
    [task] = _reminders(document)
    with as_actor(h.workspace, h.anne.id):
        services.complete_task(workspace=h.workspace, user=h.anne, task_id=task.id)
        again = services.reopen_task(workspace=h.workspace, user=h.anne, task_id=task.id)
    assert again.status == "open"

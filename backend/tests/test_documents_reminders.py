"""An end date becomes a task in "Mijn taken" (archive plan Task 8, spec S 7.1, AR6): one
named writer, one daily job, no creator, at most one open reminder per document."""

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from procrastinate.contrib.django import app as procrastinate_app

from apps.documents import reminders
from apps.documents.models import Document, DocumentEvent
from apps.tasks import services as task_services
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.archive_support import own_document
from tests.documents_support import trashed
from tests.my_tasks_support import household, ids, mine
from tests.visibility_support import as_actor, letter, office

APPS = Path(__file__).resolve().parent.parent / "apps"
TODAY = date(2026, 11, 24)
END = date(2027, 1, 23)  # 60 days after TODAY


def _reminders(document: Document) -> list[Task]:
    with as_actor(document.workspace, SYSTEM):
        return list(
            Task.objects.filter(document=document, type=Task.Type.EXPIRY).order_by("created_at")
        )


def test_reminder_appears_when_the_lead_time_starts_and_not_before() -> None:
    h = household()
    document = own_document(
        h.workspace, on=[h.persons["Anne"]], scanned_by=h.anne, expires_on=END, remind_days=30
    )
    assert reminders.start_expiry_reminders(today=END - timedelta(days=31)) == 0
    assert _reminders(document) == []
    assert reminders.start_expiry_reminders(today=END - timedelta(days=30)) == 1
    [task] = _reminders(document)
    assert (task.deadline, task.status, task.deadline_source) == (END, "open", "calculated")


def test_reminder_is_one_task_even_if_the_job_runs_twice_or_races() -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, expires_on=END, remind_days=90)
    assert reminders.start_expiry_reminders(today=TODAY) == 1
    assert reminders.start_expiry_reminders(today=TODAY) == 0
    assert len(_reminders(document)) == 1
    with as_actor(h.workspace, SYSTEM), pytest.raises(IntegrityError), transaction.atomic():
        Task.objects.create(
            workspace=h.workspace,
            document=document,
            type=Task.Type.EXPIRY,
            description="Tweede",
            deadline=END,
        )


def test_a_letters_renew_task_is_not_blocked_or_touched_by_the_index() -> None:
    h = household()
    post = letter(h.workspace, on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        for _ in range(2):
            Task.objects.create(
                workspace=h.workspace, document=post, type=Task.Type.RENEW, description="Verleng"
            )
        assert Task.objects.filter(document=post, type=Task.Type.RENEW).count() == 2
    assert reminders.start_expiry_reminders(today=TODAY) == 0


def test_reminder_has_no_creator_no_ai_flag_no_assignee_and_a_system_history_line() -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, expires_on=END, remind_days=90)
    reminders.start_expiry_reminders(today=TODAY)
    [task] = _reminders(document)
    assert (task.created_by_id, task.created_by_ai, task.assigned_to_id) == (None, False, None)
    with as_actor(h.workspace, SYSTEM):
        [line] = DocumentEvent.objects.filter(document=document, kind="reminder_started")
    assert (line.origin, line.actor_id, line.on_date) == ("system", None, END)


def test_reminder_shows_in_mijn_taken_for_the_documents_owners_and_nobody_else() -> None:
    o = office()
    document = own_document(
        o.workspace, on=[o.persons["Piet"]], scanned_by=o.plain, expires_on=END, remind_days=90
    )
    reminders.start_expiry_reminders(today=TODAY)
    [task] = _reminders(document)
    assert task.id in ids(mine(o.workspace, o.plain))
    for other in (o.finance, o.hr, o.viewer):
        assert task.id not in ids(mine(o.workspace, other, part="all")), other.name


def test_changing_the_date_moves_the_open_reminder_and_clearing_it_ends_it(
    client: Client,
) -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, expires_on=END, remind_days=90)
    reminders.start_expiry_reminders(today=TODAY)
    client.force_login(h.anne)
    url = f"/api/v1/workspaces/{h.workspace.id}/documents/{document.id}/details"
    response = client.patch(url, {"expires_on": "2027-02-10"}, content_type="application/json")
    assert response.status_code == 200
    [task] = _reminders(document)
    assert task.deadline == date(2027, 2, 10)
    assert response.json()["reminder_task_id"] == str(task.id)
    assert "10 februari 2027" in task.description
    response = client.patch(url, {"clear_expires_on": True}, content_type="application/json")
    assert response.status_code == 200
    assert _reminders(document) == []
    assert response.json()["reminder_task_id"] is None


def test_a_completed_reminder_is_not_recreated_for_the_same_date() -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, expires_on=END, remind_days=90)
    reminders.start_expiry_reminders(today=TODAY)
    [task] = _reminders(document)
    with as_actor(h.workspace, h.anne.id):
        task_services.complete_task(workspace=h.workspace, user=h.anne, task_id=task.id)
    assert reminders.start_expiry_reminders(today=TODAY) == 0
    # Renewed in place: a later end date gets a new reminder once its lead starts.
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=document.id).update(expires_on=END + timedelta(days=30))
    assert reminders.start_expiry_reminders(today=TODAY) == 1
    assert [t.status for t in _reminders(document)] == ["done", "open"]


def test_a_trashed_document_makes_no_reminder_and_its_task_hides_with_it() -> None:
    h = household()
    gone = own_document(h.workspace, scanned_by=h.anne, expires_on=END, remind_days=90)
    trashed(gone)
    assert reminders.start_expiry_reminders(today=TODAY) == 0
    kept = own_document(
        h.workspace, on=[h.persons["Anne"]], scanned_by=h.anne, expires_on=END, remind_days=90
    )
    reminders.start_expiry_reminders(today=TODAY)
    [task] = _reminders(kept)
    assert task.id in ids(mine(h.workspace, h.anne))
    trashed(kept)
    assert task.id not in ids(mine(h.workspace, h.anne, part="all"))


def test_reminder_never_changes_letter_status_or_counts_the_document_as_waiting() -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, expires_on=END, remind_days=90)
    reminders.start_expiry_reminders(today=TODAY)
    with as_actor(h.workspace, SYSTEM):
        stored = Document.objects.get(id=document.id)
    assert (stored.task_open_count, stored.task_overdue_from) == (0, None)
    with as_actor(h.workspace, SYSTEM):
        assert not DocumentEvent.objects.filter(
            document=document, kind__in=["letter_handled", "letter_reopened"]
        ).exists()


def test_description_holds_only_title_and_date() -> None:
    h = household()
    document = own_document(
        h.workspace,
        scanned_by=h.anne,
        title="Paspoort",
        expires_on=date(2031, 3, 12),
        remind_days=90,
        ocr_text="Geheime tekst van de pagina",
    )
    reminders.start_expiry_reminders(today=date(2031, 1, 1))
    [task] = _reminders(document)
    assert task.description == "Geldig tot 12 maart 2031: Paspoort"


def test_a_review_cannot_make_an_expiry_task() -> None:
    from apps.common.errors import ApiError
    from apps.documents import review
    from apps.documents.schemas import ConfirmIn

    h = household()
    document = letter(h.workspace, on=[], status="needs_review")
    data = ConfirmIn.model_validate(
        {
            "sender": "Gemeente",
            "document_type": "",
            "summary": "",
            "summary_by_ai": False,
            "tasks": [{"type": "expiry", "description": "Verloopt", "deadline_source": "manual"}],
        }
    )
    with pytest.raises(ApiError) as caught:
        review._validate(data, document)
    assert caught.value.status == 422


def test_source_test_names_the_new_task_writers() -> None:
    creators = set()
    expiry = set()
    for path in sorted(APPS.rglob("*.py")):
        relative = path.relative_to(APPS).as_posix()
        if "migrations/" in relative:
            continue
        text = path.read_text()
        if re.search(r"\bTask\.objects\.(create|bulk_create)\(", text):
            creators.add(relative)
        if "Task.Type.EXPIRY" in text and relative != "tasks/models.py":
            expiry.add(relative)
    assert creators == {"documents/review.py", "documents/reminders.py"}
    # Who names the reminder's type: its writer, the review's refusal, the answers.
    assert expiry == {"documents/reminders.py", "documents/review.py", "documents/services.py"}


def test_the_job_is_registered_daily_with_a_queueing_lock() -> None:
    task = procrastinate_app.tasks["start_expiry_reminders"]
    assert task.queueing_lock == "start_expiry_reminders"
    periodic = {
        entry.task.name: entry.cron
        for entry in procrastinate_app.periodic_registry.periodic_tasks.values()
    }
    assert periodic["start_expiry_reminders"] == "10 4 * * *"

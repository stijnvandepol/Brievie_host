"""The end date of an own document becomes a task in "Mijn taken" (archive spec S 7.1, AR6).

`start_reminder` is the one writer of a task of type `expiry`, as the system; the daily job
(`start_expiry_reminders`) asks it for every own document whose reminder lead has started.
`on_details_changed` follows a changed end date or title: it moves the open reminder, or
ends the reminder when the date is cleared; it never makes one (the job does). A document
has at most one open reminder (`tasks_task_one_reminder`, a partial unique index), and a
reminder ticked off is not made again for the same date: only a later end date (the
document renewed in place) gets a new one.

The reminder has no creator and no assignee (`deadline_source = calculated`): nobody made
it, the system did, and the history line `reminder_started` has origin `system` and no
actor. Its description is the title and the date, never the document's text. It is not
post that waits: `letter_sync` keeps an own document's letter facts at zero.
"""

import logging
from datetime import date, timedelta
from typing import Final
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import formats, timezone, translation
from django.utils.translation import gettext as _

from apps.documents import organise
from apps.documents.models import LIVE, REMIND_DAYS, Document
from apps.tasks import letter_sync
from apps.tasks.models import Task
from apps.tasks.services import delete_tasks
from apps.workspaces.context import SYSTEM, system_actor, workspace_context
from apps.workspaces.models import Workspace

logger = logging.getLogger(__name__)

# At most this many reminders per workspace per run; the rest follow the next day.
BATCH: Final = 500
DESCRIPTION_MAX: Final = 500


def _open_reminders(document: Document) -> "models.QuerySet[Task]":
    return Task.objects.filter(document=document, type=Task.Type.EXPIRY, status=Task.Status.OPEN)


def description(title: str, expires_on: date) -> str:
    """ "Geldig tot 12 maart 2031: Paspoort": the date and the title, nothing else."""
    with translation.override(settings.LANGUAGE_CODE):
        day = formats.date_format(expires_on, "j F Y")
        text = _("Geldig tot %(date)s: %(title)s") % {"date": day, "title": title}
    return text[:DESCRIPTION_MAX]


def is_due(expires_on: date, remind_days: int, today: date) -> bool:
    """Whether the reminder lead has started on `today`."""
    return expires_on - timedelta(days=remind_days) <= today


def start_reminder(document: Document, *, today: date) -> Task | None:
    """Make the reminder of the document's end date, as the system; None when it is not
    due, already open, ticked off for this same date, or the document is no reviewed own
    document outside the trash. The caller holds the document's row lock."""
    if (
        document.kind != Document.Kind.DOCUMENT
        or document.status != Document.Status.DONE
        or document.trashed_at is not None
        or document.expires_on is None
        or not is_due(document.expires_on, document.remind_days, today)
    ):
        return None
    expires_on = document.expires_on
    with system_actor():
        known = Task.objects.filter(document=document, type=Task.Type.EXPIRY)
        if known.filter(
            models.Q(status=Task.Status.OPEN)
            | models.Q(status=Task.Status.DONE, deadline__gte=expires_on)
        ).exists():
            return None
        try:
            with transaction.atomic():
                task = Task.objects.create(
                    workspace_id=document.workspace_id,
                    document=document,
                    type=Task.Type.EXPIRY,
                    description=description(document.title, expires_on),
                    deadline=expires_on,
                    deadline_source=Task.DeadlineSource.CALCULATED,
                    created_by=None,
                    created_by_ai=False,
                )
        except IntegrityError:
            # Another run made it meanwhile (`tasks_task_one_reminder`): one is enough.
            return None
        organise.record_reminder_started(document, until=expires_on)
        letter_sync.sync(document)
    return task


def on_details_changed(document: Document) -> None:
    """The document's end date or title changed (`own.update_details`, as the member, who
    may edit it; the caller holds its row lock): the open reminder follows the new date and
    title (security review of PR #54, point 5), or ends when the date is cleared. Makes no
    reminder itself."""
    reminders = _open_reminders(document)
    if document.expires_on is None:
        delete_tasks(reminders)
    else:
        for task in reminders:
            task.deadline = document.expires_on
            task.description = description(document.title, document.expires_on)
            task.save(update_fields=["deadline", "description", "updated_at"])
    letter_sync.sync(document)


def due_document_ids(workspace_id: UUID, today: date, limit: int = BATCH) -> list[UUID]:
    """The own documents of the workspace whose reminder lead has started and that have no
    open reminder, oldest end date first. As the system, in the caller's context."""
    open_reminder = Task.objects.filter(
        document=models.OuterRef("pk"), type=Task.Type.EXPIRY, status=Task.Status.OPEN
    )
    candidates = (
        Document.objects.filter(
            LIVE,
            workspace_id=workspace_id,
            kind=Document.Kind.DOCUMENT,
            status=Document.Status.DONE,
            expires_on__isnull=False,
            # The widest lead; the exact one is judged below.
            expires_on__lte=today + timedelta(days=max(REMIND_DAYS)),
        )
        .exclude(models.Exists(open_reminder))
        .order_by("expires_on", "id")
        .values_list("id", "expires_on", "remind_days")
    )
    due = []
    for document_id, expires_on, remind_days in candidates.iterator():
        if expires_on is not None and is_due(expires_on, remind_days, today):
            due.append(document_id)
            if len(due) >= limit:
                break
    return due


def start_expiry_reminders(*, today: date | None = None) -> int:
    """For every workspace: the reminders whose lead started, each in its own short
    transaction under the document's row lock, as the system. How many were made; the log
    gets counts only."""
    day = today or timezone.localdate()
    made = 0
    for workspace_id in Workspace.objects.order_by("id").values_list("id", flat=True):
        with workspace_context(workspace_id, actor=SYSTEM):
            document_ids = due_document_ids(workspace_id, day)
        for document_id in document_ids:
            with workspace_context(workspace_id, actor=SYSTEM), transaction.atomic():
                document = (
                    Document.objects.select_for_update(no_key=True)
                    .filter(LIVE, id=document_id)
                    .first()
                )
                if document is not None and start_reminder(document, today=day) is not None:
                    made += 1
    logger.info("Expiry reminders: %d started", made)
    return made

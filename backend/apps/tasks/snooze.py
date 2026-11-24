"""\"Uitgesteld tot ...\" (letter status spec S 5): a member puts a task off in their own list.

Personal: one row per task and member (`TaskSnooze`, forced row-level security with an
own-rows policy), seen only by that member, never in the history, the audit log or the
letter's status. It cannot hide a term (Besluit B2): the day lies after today, within a
year, and not after the task's own deadline. A viewer may snooze (it changes nothing
shared); emergency access may not (it is read-only).
"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from apps.common.errors import ApiError
from apps.documents import visibility
from apps.tasks import services
from apps.tasks.models import Task, TaskSnooze
from apps.tasks.waiting import bad_date, date_ok, task_done
from apps.workspaces.models import Membership


def snooze(*, membership: Membership, task_id: UUID, until: date | None) -> Task:
    """Snooze the open task for the member until `until` (None: end their snooze). The task
    is found as the member (404 `task_not_found` like an unknown id); the same day again
    changes nothing. No history line, no audit row, no change of the letter's facts."""
    with transaction.atomic():
        task = _visible(membership, task_id)
        if task.status != Task.Status.OPEN:
            raise task_done()
        if until is None:
            TaskSnooze.objects.filter(task=task, user_id=membership.user_id).delete()
            return task
        if not date_ok(until, services.task_today()):
            raise bad_date()
        if task.deadline is not None and until > task.deadline:
            raise ApiError(
                422,
                "snooze_after_deadline",
                _("Je kunt niet uitstellen tot na de datum van de taak."),
            )
        TaskSnooze.objects.update_or_create(
            workspace=membership.workspace,
            task=task,
            user_id=membership.user_id,
            defaults={"until": until},
        )
        return task


def _visible(membership: Membership, task_id: UUID) -> Task:
    """The task as the member sees it, not of a letter in the trash, without a row lock: a
    snooze changes nothing shared, so a plain read is enough (and a viewer's snooze never
    queues behind a writer). 404 like an unknown id; no snoozing through emergency access."""
    task = (
        Task.objects.filter(workspace_id=membership.workspace_id, id=task_id)
        .filter(Q(document__isnull=True) | Q(document__trashed_at__isnull=True))
        .select_related(*services.TASK_RELATED)
        .first()
    )
    if task is None:
        raise services.task_not_found()
    if task.document is not None:
        visibility.require_editable(task.document)
    return task


def snoozed_until(tasks: Sequence[Task], user_id: UUID, today: date) -> dict[UUID, date]:
    """Task id -> the day the member's own snooze ends, for the tasks of a page, while it
    is after today: one query."""
    ids = [task.id for task in tasks]
    if not ids:
        return {}
    return dict(
        TaskSnooze.objects.filter(task_id__in=ids, user_id=user_id, until__gt=today).values_list(
            "task_id", "until"
        )
    )

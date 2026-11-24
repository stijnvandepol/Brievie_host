"""\"Wacht op reactie tot ...\" (letter status spec S 5, ruling LS4): the other party has the
next move until a day. Shared: everyone who sees the letter sees it, and it counts in the
letter's status. Whoever may edit the letter may set it, as ticking a task off.

Nothing happens when the day arrives (LS7): the task is back in "Mijn taken" with
"Wachten voorbij", the letter is "Openstaand" again, nothing is written. Besluit B2: the
day may lie after the task's own deadline (the sender may grant time); the deadline stays
visible beside it.
"""

from datetime import date, timedelta
from typing import Final
from uuid import UUID

from django.db import transaction
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.documents import organise
from apps.tasks import services
from apps.tasks.models import Task
from apps.workspaces.models import Workspace

# "Within a year": the latest day, counted from today.
MAX_DAYS_AHEAD: Final = 365


def date_ok(until: date, today: date) -> bool:
    """After today and at most a year ahead."""
    return today < until <= today + timedelta(days=MAX_DAYS_AHEAD)


def bad_date() -> ApiError:
    return ApiError(422, "validation_error", _("Kies een datum na vandaag, binnen een jaar."))


def task_done() -> ApiError:
    return ApiError(409, "task_done", _("Deze taak is al afgerond."))


def set_waiting(*, workspace: Workspace, user: User, task_id: UUID, until: date | None) -> Task:
    """Let the open task wait for a reply until `until` (None: stop waiting). The same day
    again changes nothing and writes no line; otherwise one history line (`task_waiting`
    with the day, or `task_waiting_ended`) and the letter's facts follow."""
    with transaction.atomic():
        services.lock_editor(workspace.id, user.id)
        task = services.get_for_update(workspace, task_id)
        if task.status != Task.Status.OPEN:
            raise task_done()
        if until is not None and not date_ok(until, services.task_today()):
            raise bad_date()
        if task.waiting_until == until:
            return task
        task.waiting_until = until
        task.save(update_fields=["waiting_until", "updated_at"])
        if task.document is not None:
            organise.record_task_waiting(task.document, actor=user, until=until)
        services.sync_letter(task)
        return task

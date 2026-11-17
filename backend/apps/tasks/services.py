from uuid import UUID

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.documents import organise
from apps.documents.models import DocumentEvent
from apps.tasks.models import Task
from apps.workspaces.models import Workspace

LIST_MAX = 500
_STATUSES = (*Task.Status.values, "all")


def task_not_found() -> ApiError:
    return ApiError(404, "task_not_found", _("We kunnen deze taak niet vinden."))


def list_tasks(workspace: Workspace, *, status: str = "open") -> list[Task]:
    """Tasks by deadline (undated last), then by creation; needs the workspace's RLS context."""
    if status not in _STATUSES:
        raise ApiError(422, "validation_error", _("De status moet open, done of all zijn."))
    tasks = Task.objects.filter(workspace=workspace).select_related("document")
    if status != "all":
        tasks = tasks.filter(status=status)
    return list(tasks.order_by(F("deadline").asc(nulls_last=True), "created_at", "id")[:LIST_MAX])


def complete_task(*, workspace: Workspace, user: User, task_id: UUID) -> Task:
    """Mark the task done; asking again changes nothing and writes no second event."""
    with transaction.atomic():
        task = _get_for_update(workspace, task_id)
        if task.status != Task.Status.DONE:
            task.status = Task.Status.DONE
            task.completed_at = timezone.now()
            task.save(update_fields=["status", "completed_at", "updated_at"])
            _record(task, DocumentEvent.Kind.TASK_COMPLETED, user)
        return task


def reopen_task(*, workspace: Workspace, user: User, task_id: UUID) -> Task:
    """Make the task open again; asking again changes nothing and writes no second event."""
    with transaction.atomic():
        task = _get_for_update(workspace, task_id)
        if task.status != Task.Status.OPEN:
            task.status = Task.Status.OPEN
            task.completed_at = None
            task.save(update_fields=["status", "completed_at", "updated_at"])
            _record(task, DocumentEvent.Kind.TASK_REOPENED, user)
        return task


def _get_for_update(workspace: Workspace, task_id: UUID) -> Task:
    task = (
        Task.objects.select_for_update(of=("self",))
        .select_related("document")
        .filter(workspace=workspace, id=task_id)
        .first()
    )
    if task is None:
        raise task_not_found()
    return task


def _record(task: Task, kind: str, user: User) -> None:
    if task.document is not None:
        organise.record_task_event(task.document, kind, user)

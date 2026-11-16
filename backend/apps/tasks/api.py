from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from apps.accounts.services import current_user
from apps.common.schemas import AUTH_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.tasks import services
from apps.tasks.models import Task
from apps.tasks.schemas import TaskItemOut
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["tasks"])

_TASKS = "/workspaces/{workspace_id}/tasks"
_TASK = "/workspaces/{workspace_id}/tasks/{task_id}"


@router.get(
    _TASKS,
    response={200: list[TaskItemOut], 404: ErrorOut, 422: ErrorOut, **AUTH_ERRORS},
    operation_id="listTasks",
    description=(
        "`status` is `open` (default), `done` or `all`. By deadline, undated last; at most "
        "500. 422 `validation_error` for another status."
    ),
)
def list_tasks(request: HttpRequest, workspace_id: UUID, status: str = "open") -> list[Task]:
    membership = workspace_member(request, workspace_id)
    return services.list_tasks(membership.workspace, status=status)


@router.post(
    f"{_TASK}/complete",
    response={200: TaskItemOut, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="completeTask",
    description="Idempotent. Writes a `task_completed` event on the task's document.",
)
def complete_task(request: HttpRequest, workspace_id: UUID, task_id: UUID) -> Task:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    return services.complete_task(
        workspace=membership.workspace, user=current_user(request), task_id=task_id
    )


@router.post(
    f"{_TASK}/reopen",
    response={200: TaskItemOut, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="reopenTask",
    description="Idempotent. Writes a `task_reopened` event on the task's document.",
)
def reopen_task(request: HttpRequest, workspace_id: UUID, task_id: UUID) -> Task:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    return services.reopen_task(
        workspace=membership.workspace, user=current_user(request), task_id=task_id
    )

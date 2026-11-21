from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from apps.accounts.services import current_user
from apps.common.schemas import AUTH_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.tasks import services
from apps.tasks.schemas import (
    MyTaskCountsOut,
    MyTasksOut,
    TaskItemOut,
    my_task_counts_out,
    my_tasks_out,
    task_item_out,
)
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
def list_tasks(request: HttpRequest, workspace_id: UUID, status: str = "open") -> list[TaskItemOut]:
    membership = workspace_member(request, workspace_id)
    tasks = services.list_tasks(membership.workspace, status=status)
    today = services.task_today()
    can_edit = membership.has_role(Membership.Role.MEMBER)
    return [task_item_out(task, can_edit=can_edit, today=today) for task in tasks]


@router.get(
    "/workspaces/{workspace_id}/my-tasks",
    response={200: MyTasksOut, 404: ErrorOut, 422: ErrorOut, **AUTH_ERRORS},
    operation_id="listMyTasks",
    description=(
        '"Mijn taken": the caller\'s tasks, derived from the post they may read. `part` is '
        "`mine` (default: given to them, or on their post), `to_pick_up` (an "
        "organisation's department and unnamed post; empty at home) or `all` (every task "
        "they see). `status` is `open` (default; by deadline, undated last, at most 500) "
        "or `done` (completed in the last 30 days, newest first, at most 200). 422 "
        "`validation_error` for another part or status."
    ),
)
def list_my_tasks(
    request: HttpRequest, workspace_id: UUID, part: str = "mine", status: str = "open"
) -> MyTasksOut:
    membership = workspace_member(request, workspace_id)
    result = services.my_tasks(membership, part=part, status=status)
    return my_tasks_out(result, can_edit=membership.has_role(Membership.Role.MEMBER))


@router.get(
    "/workspaces/{workspace_id}/my-tasks/counts",
    response={200: MyTaskCountsOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="countMyTasks",
    description='The numbers of "Mijn taken" for the navigation: open tasks only.',
)
def count_my_tasks(request: HttpRequest, workspace_id: UUID) -> MyTaskCountsOut:
    membership = workspace_member(request, workspace_id)
    return my_task_counts_out(services.count_my_tasks(membership))


@router.post(
    f"{_TASK}/complete",
    response={200: TaskItemOut, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="completeTask",
    description="Idempotent. Writes a `task_completed` event on the task's document.",
)
def complete_task(request: HttpRequest, workspace_id: UUID, task_id: UUID) -> TaskItemOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    task = services.complete_task(
        workspace=membership.workspace, user=current_user(request), task_id=task_id
    )
    return task_item_out(task, can_edit=True, today=services.task_today())


@router.post(
    f"{_TASK}/reopen",
    response={200: TaskItemOut, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="reopenTask",
    description="Idempotent. Writes a `task_reopened` event on the task's document.",
)
def reopen_task(request: HttpRequest, workspace_id: UUID, task_id: UUID) -> TaskItemOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    task = services.reopen_task(
        workspace=membership.workspace, user=current_user(request), task_id=task_id
    )
    return task_item_out(task, can_edit=True, today=services.task_today())

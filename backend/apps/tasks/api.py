from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from apps.accounts.services import current_user
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.tasks import assignment, services
from apps.tasks.models import Task
from apps.tasks.schemas import (
    AssignIn,
    DeadlineIn,
    MyTaskCountsOut,
    MyTasksOut,
    TaskAssigneeOut,
    TaskItemOut,
    my_task_counts_out,
    my_tasks_out,
    task_item_out,
)
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["tasks"])


def _one(membership: Membership, task: Task) -> TaskItemOut:
    """A task after a change, with the caller's flags."""
    flags = assignment.flags_for_tasks(membership, [task])[task.id]
    return task_item_out(task, can_edit=True, flags=flags, today=services.task_today())


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
    flags = assignment.flags_for_tasks(membership, tasks)
    return [
        task_item_out(task, can_edit=can_edit, flags=flags[task.id], today=today) for task in tasks
    ]


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
    flags = assignment.flags_for_tasks(membership, [item.task for item in result.items])
    return my_tasks_out(result, can_edit=membership.has_role(Membership.Role.MEMBER), flags=flags)


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
    return _one(membership, task)


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
    return _one(membership, task)


@router.post(
    f"{_TASK}/assign",
    response={200: TaskItemOut, 404: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="assignTask",
    description=(
        "Give the task to a member who can read its letter (`user_id`), or take it back "
        "(`user_id` null). The same assignee again changes nothing. Writes a "
        "`task_assigned` or `task_unassigned` event on the letter. 404 `task_not_found` "
        "for a task the caller does not see; 422 `task_without_letter`; 422 "
        "`assignee_cannot_read` for someone who may not read the letter, a viewer or no "
        "member."
    ),
)
def assign_task(
    request: HttpRequest, workspace_id: UUID, task_id: UUID, payload: AssignIn
) -> TaskItemOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    task = assignment.assign(
        membership=membership,
        user=current_user(request),
        task_id=task_id,
        assignee_id=payload.user_id,
    )
    return _one(membership, task)


@router.get(
    f"{_TASK}/assignees",
    response={200: list[TaskAssigneeOut], 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="listTaskAssignees",
    description=(
        "The members the task can be given to: those who can read its letter, viewers "
        "left out, by name. Empty for a task without a letter."
    ),
)
def list_task_assignees(
    request: HttpRequest, workspace_id: UUID, task_id: UUID
) -> list[TaskAssigneeOut]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    return [
        TaskAssigneeOut(user_id=a.user_id, name=a.name, is_me=a.is_me)
        for a in assignment.assignees(membership, task_id)
    ]


@router.post(
    f"{_TASK}/deadline",
    response={200: TaskItemOut, 404: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="setTaskDeadline",
    description=(
        '"Datum invullen": the date the letter names, filled in by hand (`deadline`; null '
        "takes it away). The source becomes `manual`; the term's words stay. The same date "
        "again changes nothing. Writes a `task_deadline_set` event on the letter. 422 "
        "`validation_error` for a date before the letter's date."
    ),
)
def set_task_deadline(
    request: HttpRequest, workspace_id: UUID, task_id: UUID, payload: DeadlineIn
) -> TaskItemOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    task = services.set_deadline(
        workspace=membership.workspace,
        user=current_user(request),
        task_id=task_id,
        deadline=payload.deadline,
    )
    return _one(membership, task)

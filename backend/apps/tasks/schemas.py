from datetime import date, datetime
from uuid import UUID

from ninja import Field, Schema

from apps.common.schemas import InputSchema
from apps.tasks.assignment import NO_FLAGS, TaskFlags
from apps.tasks.mine import days_left, group_for
from apps.tasks.models import Task, amount_text
from apps.tasks.services import MyTask, MyTaskCounts, MyTaskList, task_today


class TaskItemOut(Schema):
    id: UUID
    type: str
    description: str
    deadline: date | None
    deadline_source: str
    # "open" or "done".
    status: str
    completed_at: datetime | None
    document_id: UUID | None
    # The document's confirmed sender; empty without a document.
    sender: str
    updated_at: datetime
    # True when the autopilot made it while confirming the letter ("Automatisch door AI").
    created_by_ai: bool = False
    # "Mijn taken" (spec S 11); every field has a default, so older apps keep decoding.
    # The letter's words the deadline was read from; empty unless `in_document`.
    deadline_quote: str = ""
    # For a task without a date: the letter's words that name a term.
    term_quote: str = ""
    # Open, without a date, and the letter names a term: "Datum nodig".
    needs_deadline: bool = False
    # What a payment costs, a decimal string ("1234.56"); empty when unknown.
    amount: str = ""
    # Days until the deadline (Europe/Amsterdam): 0 today, negative when overdue; null
    # without a date or when done.
    days_left: int | None = None
    # "overdue", "within_week", "later" or "no_date"; empty when done.
    group: str = ""
    # Why it is the member's: "assigned", "addressee", "department", "unassigned" or
    # "other"; empty in `listTasks`.
    reason: str = ""
    # The names of the letter's persons and departments other than the caller's own
    # persons ("Voor jou en Piet", "Voor Finance"); empty in `listTasks`.
    for_names: list[str] = Field(default_factory=list)
    assigned_to_id: UUID | None = None
    assigned_to_name: str = ""
    assigned_by_name: str = ""
    # Who ticked it off ("Afgevinkt door Anne"); empty for older tasks.
    completed_by_name: str = ""
    # The letter's type code; empty without a letter.
    document_type: str = ""
    # Whether the caller may tick it off, date it or give it to someone (role member+).
    can_edit: bool = False
    # Who may change who does it (navigation spec S 7): "Ik doe dit" (nobody has it),
    # "Geef aan…" (free, yours, or as an admin), "Niet meer aan mij" / "Vrijgeven" (yours,
    # or as an admin). Computed by the server's own rule; false for a viewer.
    can_take: bool = False
    can_hand_over: bool = False
    can_release: bool = False
    # "Wacht op reactie tot" (letter status spec S 5): the day the other party has the next
    # move until; shared, null when the task does not wait. Cleared when it is done.
    waiting_until: date | None = None
    # The waiting day is today or past and still set: "Wachten voorbij sinds ...".
    wait_over: bool = False
    # "Uitgesteld tot": the caller's own snooze, only while after today; nobody else's.
    snoozed_until: date | None = None


class MyTasksOut(Schema):
    items: list[TaskItemOut]
    # Today in Europe/Amsterdam, the day `days_left` counts from.
    today: date
    # False when the member's account is linked to no person: only tasks given to them.
    has_person: bool
    # More open tasks than the 500 shown (or done than the 200 shown).
    truncated: bool
    # Letters the member sees that wait in "Inbox" (check or name); 0 unless part
    # `mine` and status `open`.
    waiting_letters: int
    # An organisation: department and unnamed post are a part of their own ("Op te pakken").
    split: bool


class MyTaskCountsOut(Schema):
    # Open tasks in "Mijn taken", and of those: overdue, due within 7 days, needing a date.
    mine: int
    overdue: int
    within_week: int
    needs_date: int
    # Open tasks in "Op te pakken"; always 0 at home.
    to_pick_up: int
    # Tasks that wait for a reply, and the caller's own snoozed ones: left out of the numbers
    # above (letter status spec S 6.3).
    waiting: int = 0
    snoozed: int = 0


class TaskAssigneeOut(Schema):
    user_id: UUID
    name: str
    is_me: bool


class AssignIn(InputSchema):
    # The member to give the task to; null releases it.
    user_id: UUID | None = None


class WaitingIn(InputSchema):
    # The day the task waits until (after today, within a year); null stops waiting.
    until: date | None = None


class SnoozeIn(InputSchema):
    # The day the task comes back in the caller's list (after today, within a year, not
    # after the task's own date); null ends the snooze.
    until: date | None = None


class DeadlineIn(InputSchema):
    # The date the letter names; null takes a filled-in date away again.
    deadline: date | None = None


def _name(user: object) -> str:
    return str(getattr(user, "name", "")) if user is not None else ""


def task_item_out(
    obj: Task | MyTask,
    *,
    can_edit: bool,
    flags: TaskFlags,
    today: date | None = None,
    snoozed: date | None = None,
) -> TaskItemOut:
    """A task as the lists show it: from a bare task (`listTasks`, a tick) or from a
    "Mijn taken" row with its reason, group and names. `flags`: what the caller may do
    with its assignment (`assignment.flags_for_tasks`)."""
    task = obj.task if isinstance(obj, MyTask) else obj
    document = task.document
    is_open = task.status == Task.Status.OPEN
    current = today or task_today()
    if isinstance(obj, MyTask):
        group = obj.group.value if obj.group is not None else ""
        left = obj.days_left
        reason = obj.reason.value
        for_names = obj.for_names
    else:
        group = group_for(task.deadline, today).value if is_open and today else ""
        left = days_left(task.deadline, today) if is_open and today else None
        reason = ""
        for_names = []
    return TaskItemOut(
        id=task.id,
        type=task.type,
        description=task.description,
        deadline=task.deadline,
        deadline_source=task.deadline_source,
        status=task.status,
        completed_at=task.completed_at,
        document_id=task.document_id,
        sender=document.sender if document is not None else "",
        updated_at=task.updated_at,
        created_by_ai=task.created_by_ai,
        deadline_quote=task.deadline_quote,
        term_quote=task.term_quote,
        needs_deadline=is_open and task.deadline is None and bool(task.term_quote),
        amount=amount_text(task.amount),
        days_left=left,
        group=group,
        reason=reason,
        for_names=for_names,
        assigned_to_id=task.assigned_to_id,
        assigned_to_name=_name(task.assigned_to),
        assigned_by_name=_name(task.assigned_by),
        completed_by_name=_name(task.completed_by),
        document_type=document.document_type if document is not None else "",
        can_edit=can_edit,
        can_take=flags.can_take,
        can_hand_over=flags.can_hand_over,
        can_release=flags.can_release,
        waiting_until=task.waiting_until,
        wait_over=task.waiting_until is not None and task.waiting_until <= current,
        snoozed_until=snoozed if snoozed is not None and snoozed > current else None,
    )


def my_tasks_out(
    result: MyTaskList,
    *,
    can_edit: bool,
    flags: dict[UUID, TaskFlags],
    snoozed: dict[UUID, date] | None = None,
) -> MyTasksOut:
    own = snoozed or {}
    return MyTasksOut(
        items=[
            task_item_out(
                item,
                can_edit=can_edit,
                flags=flags.get(item.task.id, NO_FLAGS),
                today=result.today,
                snoozed=own.get(item.task.id),
            )
            for item in result.items
        ],
        today=result.today,
        has_person=result.has_person,
        truncated=result.truncated,
        waiting_letters=result.waiting_letters,
        split=result.split,
    )


def my_task_counts_out(counts: MyTaskCounts) -> MyTaskCountsOut:
    return MyTaskCountsOut(
        mine=counts.mine,
        overdue=counts.overdue,
        within_week=counts.within_week,
        needs_date=counts.needs_date,
        to_pick_up=counts.to_pick_up,
        waiting=counts.waiting,
        snoozed=counts.snoozed,
    )

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Q, QuerySet, Value, When
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.documents import organise
from apps.documents.models import DocumentEvent
from apps.people.models import Person
from apps.tasks.facts import member_facts
from apps.tasks.mine import (
    PARTS,
    WEEK,
    Group,
    MemberFacts,
    Part,
    Reason,
    TaskFacts,
    days_left,
    group_for,
    reason_for,
)
from apps.tasks.models import Task
from apps.workspaces.models import Membership, Workspace

LIST_MAX = 500
# What a list row names besides the task: its letter and the people around it.
TASK_RELATED: Final = ("document", "assigned_to", "assigned_by", "completed_by")
_STATUSES = (*Task.Status.values, "all")


def task_not_found() -> ApiError:
    return ApiError(404, "task_not_found", _("We kunnen deze taak niet vinden."))


def list_tasks(workspace: Workspace, *, status: str = "open") -> list[Task]:
    """Tasks by deadline (undated last), then by creation; needs the workspace's RLS context.

    The task policy follows the letter; a task of a letter in the trash is left out here.
    """
    if status not in _STATUSES:
        raise ApiError(422, "validation_error", _("De status moet open, done of all zijn."))
    tasks = (
        Task.objects.filter(workspace=workspace)
        .filter(Q(document__isnull=True) | Q(document__trashed_at__isnull=True))
        .select_related(*TASK_RELATED)
    )
    if status != "all":
        tasks = tasks.filter(status=status)
    return list(tasks.order_by(F("deadline").asc(nulls_last=True), "created_at", "id")[:LIST_MAX])


def complete_task(
    *,
    workspace: Workspace,
    user: User,
    task_id: UUID,
    origin: str = DocumentEvent.Origin.USER,
) -> Task:
    """Mark the task done; asking again changes nothing and writes no second event.

    The one place that completes a task. `origin` is `merge` when a merge of duplicate
    letters completes the open twin of a task done on the other scan.
    """
    with transaction.atomic():
        task = _get_for_update(workspace, task_id)
        if task.status != Task.Status.DONE:
            task.status = Task.Status.DONE
            task.completed_at = timezone.now()
            task.save(update_fields=["status", "completed_at", "updated_at"])
            _record(task, DocumentEvent.Kind.TASK_COMPLETED, user, origin)
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
        .select_related(*TASK_RELATED)
        .filter(workspace=workspace, id=task_id)
        .filter(Q(document__isnull=True) | Q(document__trashed_at__isnull=True))
        .first()
    )
    if task is None:
        raise task_not_found()
    return task


def _record(task: Task, kind: str, user: User, origin: str = DocumentEvent.Origin.USER) -> None:
    if task.document is not None:
        organise.record_task_event(task.document, kind, user, origin)


# "Mijn taken" (spec S 5 to 7, 11): derived from the post on every read, never stored.
MY_LIST_MAX: Final = 500
DONE_MAX: Final = 200
DONE_DAYS: Final = 30
# Today and days left are counted in the Netherlands, as the notices are.
TASK_ZONE: Final = ZoneInfo("Europe/Amsterdam")


@dataclass(frozen=True)
class MyTask:
    task: Task
    reason: Reason
    group: Group | None  # None for a done task
    days_left: int | None
    for_names: list[str]


@dataclass(frozen=True)
class MyTaskList:
    items: list[MyTask]
    today: date
    has_person: bool
    truncated: bool
    waiting_letters: int
    split: bool


@dataclass(frozen=True)
class MyTaskCounts:
    mine: int
    overdue: int
    within_week: int
    needs_date: int
    to_pick_up: int


def task_today(now: datetime | None = None) -> date:
    """Today in the Netherlands (at 23:30 UTC on the 30th it is already the 1st)."""
    return timezone.localtime(now or timezone.now(), TASK_ZONE).date()


def _overlap(ids: Collection[UUID]) -> Q | None:
    return Q(document__addressee_ids__overlap=sorted(ids)) if ids else None


def _reason_filter(reason: Reason, facts: MemberFacts) -> Q | None:
    """The query arm of one reason, exactly `mine.reason_for`'s order (an assignee first,
    then own person, own department, unnamed post for a sorter). None: matches nothing."""
    me = facts.user_id
    if reason == Reason.ASSIGNED:
        return Q(assigned_to_id=me)
    unassigned_letter = Q(assigned_to__isnull=True, document__isnull=False)
    persons = _overlap(facts.person_ids)
    departments = _overlap(facts.department_ids)
    if reason == Reason.ADDRESSEE:
        own = Q(assigned_to__isnull=True, document__isnull=True, created_by_id=me)
        return own if persons is None else own | (unassigned_letter & persons)
    if reason == Reason.DEPARTMENT:
        if departments is None:
            return None
        arm = unassigned_letter & departments
        return arm if persons is None else arm & ~persons
    if reason == Reason.UNASSIGNED:
        if not facts.sorts_post:
            return None
        arm = unassigned_letter
        for taken in (persons, departments, _overlap(facts.covered_ids)):
            if taken is not None:
                arm &= ~taken
        return arm
    raise ValueError(reason)  # `other` is only ever the rest, in part "all"


def _part_filter(part: str, facts: MemberFacts) -> Q | None:
    """The part's condition; None for every visible task (part "all")."""
    reasons = PARTS[(facts.split, part)]
    if Reason.OTHER in reasons:
        return None
    condition = Q(pk__in=[])
    for reason in sorted(reasons):
        arm = _reason_filter(reason, facts)
        if arm is not None:
            condition |= arm
    return condition


def _live(workspace: Workspace) -> QuerySet[Task]:
    """The workspace's tasks the member sees (the task policy, as the request's actor),
    without those of letters in the trash."""
    return Task.objects.filter(workspace=workspace).filter(
        Q(document__isnull=True) | Q(document__trashed_at__isnull=True)
    )


def _parts() -> str:
    return _("Kies mine, to_pick_up of all.")


def my_tasks(membership: Membership, *, part: str = "mine", status: str = "open") -> MyTaskList:
    """The member's tasks of one part: open by date (undated last, those that need a date
    first among them), or done in the last 30 days, newest first. Runs as the member: the
    task policy decides what is visible, the part only narrows. 422 for another part or
    status; "to_pick_up" at home is empty (S 5.2)."""
    if part not in Part.__members__.values():
        raise ApiError(422, "validation_error", _parts())
    if status not in Task.Status.values:
        raise ApiError(422, "validation_error", _("De status moet open of done zijn."))
    facts = member_facts(membership)
    today = task_today()
    reasons = PARTS[(facts.split, part)]
    tasks = _live(membership.workspace).filter(status=status)
    condition = _part_filter(part, facts)
    if condition is not None:
        tasks = tasks.filter(condition)
    tasks = tasks.select_related(*TASK_RELATED)
    if status == Task.Status.OPEN:
        cap = MY_LIST_MAX
        ordered = tasks.order_by(
            F("deadline").asc(nulls_last=True),
            Case(When(term_quote="", then=Value(1)), default=Value(0), output_field=IntegerField()),
            "created_at",
            "id",
        )
    else:
        cap = DONE_MAX
        since = timezone.now() - timedelta(days=DONE_DAYS)
        ordered = tasks.filter(completed_at__gte=since).order_by("-completed_at", "-id")
    rows = list(ordered[: cap + 1]) if reasons else []
    truncated = len(rows) > cap
    rows = rows[:cap]
    names = _names({p for t in rows if t.document is not None for p in t.document.addressee_ids})
    items = []
    for task in rows:
        reason = reason_for(_task_facts(task), facts)
        if reason not in reasons:
            continue  # the query and the rule agree (tested); this only ever narrows
        open_task = task.status == Task.Status.OPEN
        items.append(
            MyTask(
                task=task,
                reason=reason,
                group=group_for(task.deadline, today) if open_task else None,
                days_left=days_left(task.deadline, today) if open_task else None,
                for_names=_for_names(task, names),
            )
        )
    # Imported here: `documents.services` reaches this module through the duplicates.
    from apps.documents.services import waiting_letters

    waiting = waiting_letters(membership) if part == Part.MINE and status == Task.Status.OPEN else 0
    return MyTaskList(
        items=items,
        today=today,
        has_person=bool(facts.person_ids),
        truncated=truncated,
        waiting_letters=waiting,
        split=facts.split,
    )


def count_my_tasks(membership: Membership) -> MyTaskCounts:
    """The numbers of the navigation and the headings: one aggregate over the member's
    open tasks, under the task policy like the list."""
    facts = member_facts(membership)
    today = task_today()
    mine = _part_filter(Part.MINE, facts) or Q()
    numbers = {
        "mine": Count("id", filter=mine),
        "overdue": Count("id", filter=mine & Q(deadline__lt=today)),
        "within_week": Count(
            "id", filter=mine & Q(deadline__gte=today, deadline__lte=today + WEEK)
        ),
        "needs_date": Count("id", filter=mine & Q(deadline__isnull=True) & ~Q(term_quote="")),
    }
    rows = mine
    if PARTS[(facts.split, Part.TO_PICK_UP)]:
        pick_up = _part_filter(Part.TO_PICK_UP, facts) or Q()
        numbers["to_pick_up"] = Count("id", filter=pick_up)
        rows = mine | pick_up
    counts = (
        _live(membership.workspace)
        .filter(status=Task.Status.OPEN)
        .filter(rows)
        .aggregate(**numbers)
    )
    return MyTaskCounts(
        mine=int(counts["mine"]),
        overdue=int(counts["overdue"]),
        within_week=int(counts["within_week"]),
        needs_date=int(counts["needs_date"]),
        to_pick_up=int(counts.get("to_pick_up", 0)),
    )


def _task_facts(task: Task) -> TaskFacts:
    document = task.document
    return TaskFacts(
        assigned_to_id=task.assigned_to_id,
        created_by_id=task.created_by_id,
        has_letter=document is not None,
        addressee_ids=frozenset(document.addressee_ids) if document is not None else frozenset(),
    )


def _names(person_ids: set[UUID]) -> dict[UUID, str]:
    """Display names of the letters' persons and departments: one query for the page (a
    person list is a directory, S 4.10)."""
    if not person_ids:
        return {}
    return dict(Person.objects.filter(id__in=person_ids).values_list("id", "display_name"))


def _for_names(task: Task, names: dict[UUID, str]) -> list[str]:
    if task.document is None:
        return []
    found = [names[p] for p in task.document.addressee_ids if p in names]
    return sorted(found, key=str.casefold)

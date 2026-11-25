from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import (
    Case,
    Count,
    F,
    IntegerField,
    Q,
    QuerySet,
    Value,
    When,
    prefetch_related_objects,
)
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.documents import organise, visibility
from apps.documents.models import LIVE, Document, DocumentEvent
from apps.people.models import Person
from apps.tasks import letter_sync
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
from apps.tasks.models import Task, TaskSnooze
from apps.workspaces.context import system_actor
from apps.workspaces.locks import lock_own_role
from apps.workspaces.models import Membership, Workspace

LIST_MAX = 500
# What a list row names besides the task: its letter and the people around it.
TASK_RELATED: Final = ("document", "assigned_to", "assigned_by", "completed_by")
# A task row never shows its letter's text: the lists leave it (and the search vector,
# about as large) in the database (measured, DECISIONS 2026-11-25).
LETTER_TEXT: Final = ("document__ocr_text", "document__search_vector")
_STATUSES = (*Task.Status.values, "all")


def task_not_found() -> ApiError:
    return ApiError(404, "task_not_found", _("We kunnen deze taak niet vinden."))


def list_tasks(workspace: Workspace, *, status: str = "open") -> list[Task]:
    """Tasks by deadline (undated last), then by creation; needs the workspace's RLS context.

    The task policy follows the letter; a task of a letter in the trash is left out here.

    The page's ids first, in the order of `tasks_task_list_order`, so the policy runs only
    until the page is full; then the letters of those rows only (measured, DECISIONS
    2026-11-25: joined into the ordered query, every visible task's letter row was read
    before the sort).
    """
    if status not in _STATUSES:
        raise ApiError(422, "validation_error", _("De status moet open, done of all zijn."))
    tasks = Task.objects.filter(workspace=workspace).filter(
        Q(document__isnull=True) | Q(document__trashed_at__isnull=True)
    )
    if status != "all":
        tasks = tasks.filter(status=status)
    ids = list(
        tasks.order_by(F("deadline").asc(nulls_last=True), "created_at", "id").values_list(
            "id", flat=True
        )[:LIST_MAX]
    )
    by_id = Task.objects.select_related("document").defer(*LETTER_TEXT).in_bulk(ids)
    return _with_people([by_id[task_id] for task_id in ids if task_id in by_id])


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
        task = get_for_update(workspace, task_id)
        if complete_locked(task, user, origin):
            sync_letter(task)
        return task


def complete_locked(task: Task, user: User, origin: str = DocumentEvent.Origin.USER) -> bool:
    """The completion itself, for a task the caller locked (letter first) and may edit;
    whether anything changed. The caller syncs the letter's facts afterwards
    (`complete_task` per task, "Brief afhandelen" once for all)."""
    if task.status == Task.Status.DONE:
        return False
    task.status = Task.Status.DONE
    task.completed_at = timezone.now()
    # "Afgevinkt door Anne"; a merge's completion keeps the person who merged.
    task.completed_by = user
    # A done task waits for nothing (no line of its own: the completion says it).
    task.waiting_until = None
    # Nobody's snooze of a done task is left (the own-rows policy does not reach other
    # members' rows: the system deletes them).
    with system_actor():
        TaskSnooze.objects.filter(task_id=task.id).delete()
    task.save(
        update_fields=["status", "completed_at", "completed_by", "waiting_until", "updated_at"]
    )
    _record(task, DocumentEvent.Kind.TASK_COMPLETED, user, origin)
    return True


def delete_tasks(tasks: QuerySet[Task]) -> None:
    """Delete tasks, and every member's snooze of them first (as the system: the own-rows
    policy lets a member delete only their own rows; the database cascade is the backstop).
    The one way application code deletes tasks as a member."""
    with system_actor():
        TaskSnooze.objects.filter(task__in=tasks.values("id")).delete()
    tasks.delete()


def reopen_task(*, workspace: Workspace, user: User, task_id: UUID) -> Task:
    """Make the task open again; asking again changes nothing and writes no second event."""
    with transaction.atomic():
        task = get_for_update(workspace, task_id)
        if task.status != Task.Status.OPEN:
            task.status = Task.Status.OPEN
            task.completed_at = None
            task.completed_by = None
            task.save(update_fields=["status", "completed_at", "completed_by", "updated_at"])
            _record(task, DocumentEvent.Kind.TASK_REOPENED, user)
            sync_letter(task)
        return task


def set_deadline(*, workspace: Workspace, user: User, task_id: UUID, deadline: date | None) -> Task:
    """ "Datum invullen": the date the letter names, filled in by hand (source `manual`,
    no quote; the term's words stay). The same date again writes nothing. 422 for a date
    before the letter's date; 404 like an unknown id for a task the caller does not see."""
    with transaction.atomic():
        task = get_for_update(workspace, task_id)
        letter_date = task.document.letter_date if task.document is not None else None
        if deadline is not None and letter_date is not None and deadline < letter_date:
            raise ApiError(422, "validation_error", _("Die datum ligt voor de datum van de brief."))
        if task.deadline == deadline and task.deadline_source == Task.DeadlineSource.MANUAL:
            return task
        task.deadline = deadline
        task.deadline_source = Task.DeadlineSource.MANUAL
        task.deadline_quote = ""
        task.save(update_fields=["deadline", "deadline_source", "deadline_quote", "updated_at"])
        _record(task, DocumentEvent.Kind.TASK_DEADLINE_SET, user)
        sync_letter(task)
        return task


def get_for_update(workspace: Workspace, task_id: UUID) -> Task:
    """The task, locked for a change, as the member. Lock order (spec S 10): the letter's
    row first (`FOR NO KEY UPDATE`, as the confirm and the merge take it), then the task;
    a task without a letter is locked alone. 404 like an unknown id for a task or letter
    the member does not see or that is in the trash; `require_editable` for the rest."""
    row = Task.objects.filter(workspace=workspace, id=task_id).values_list("document_id").first()
    if row is None:
        raise task_not_found()
    document_id = row[0]
    if document_id is not None and not (
        Document.objects.select_for_update(no_key=True)
        .filter(LIVE, workspace=workspace, id=document_id)
        .values_list("id", flat=True)
        .first()
    ):
        raise task_not_found()
    task = (
        Task.objects.select_for_update(of=("self",))
        .select_related(*TASK_RELATED)
        .filter(workspace=workspace, id=task_id, document_id=document_id)
        .first()
    )
    # Moved by a merge between the two reads: its old letter is in the trash now.
    if task is None:
        raise task_not_found()
    if task.document is not None:
        visibility.require_editable(task.document)
    return task


def lock_editor(workspace_id: UUID, user_id: UUID) -> None:
    """The first lock of a task change that also sets or ends something shared: the
    caller's own membership, its role read under the lock. A viewer (also one demoted
    while the request ran) may not change what others see: 403 `forbidden`."""
    if lock_own_role(workspace_id, user_id) == Membership.Role.VIEWER:
        raise ApiError(403, "forbidden", _("Je mag dit niet doen in deze werkruimte."))


def sync_letter(task: Task) -> None:
    if task.document is not None:
        letter_sync.sync(task.document)


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
    # Open tasks that wait for a reply, and the member's snoozed ones (letter status S 6.3).
    waiting: int = 0
    snoozed: int = 0


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


def active_q(today: date) -> Q:
    """The task does not wait for a reply, or the day it waits until has arrived (letter
    status spec S 4.1: a waiting day of today or past is active again)."""
    return Q(waiting_until__isnull=True) | Q(waiting_until__lte=today)


def snoozed_by(user_id: UUID, today: date) -> Q:
    """The member put the task off and the day is still ahead (their own rows only: the
    own-rows policy hides everyone else's). The ids are read once, a handful, so no query
    probes the snooze table (and its nested task policy) once per task."""
    ids = list(
        TaskSnooze.objects.filter(user_id=user_id, until__gt=today).values_list(
            "task_id", flat=True
        )
    )
    return Q(id__in=ids)


def _parts() -> str:
    return _("Kies mine, to_pick_up of all.")


def my_tasks(
    membership: Membership,
    *,
    part: str = "mine",
    status: str = "open",
    include_waiting: bool = False,
) -> MyTaskList:
    """The member's tasks of one part: open by date (undated last, those that need a date
    first among them), or done in the last 30 days, newest first. Runs as the member: the
    task policy decides what is visible, the part only narrows. 422 for another part or
    status; "to_pick_up" at home is empty (S 5.2). Open tasks that wait for a reply (shared)
    or that the member snoozed (their own) are not in the date groups; with
    `include_waiting` they follow in the groups `waiting` and `snoozed` (letter status S
    6.3), each capped inside the list maximum."""
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
    tasks = tasks.select_related("document").defer(*LETTER_TEXT)
    active = active_q(today)
    mine_snoozed = snoozed_by(facts.user_id, today)
    if status == Task.Status.OPEN:
        cap = MY_LIST_MAX
        everything = tasks
        tasks = tasks.filter(active & ~mine_snoozed)
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
    rows = _with_people(rows[:cap])
    parked: dict[UUID, Group] = {}
    if include_waiting and status == Task.Status.OPEN and reasons:
        waiting_rows = list(
            everything.filter(~active).order_by("waiting_until", "created_at", "id")[:cap]
        )
        until = dict(
            TaskSnooze.objects.filter(user_id=facts.user_id, until__gt=today).values_list(
                "task_id", "until"
            )
        )
        snoozed_rows = sorted(
            everything.filter(active & mine_snoozed).order_by("created_at", "id")[:cap],
            key=lambda t: until.get(t.id, today),
        )
        parked = {t.id: Group.WAITING for t in waiting_rows} | {
            t.id: Group.SNOOZED for t in snoozed_rows
        }
        rows = rows + _with_people(waiting_rows + snoozed_rows)
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
                group=parked.get(task.id)
                or (group_for(task.deadline, today) if open_task else None),
                days_left=days_left(task.deadline, today) if open_task else None,
                for_names=_for_names(task, names, facts.person_ids),
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
    whole = _part_filter(Part.MINE, facts) or Q()
    active = active_q(today)
    mine_snoozed = snoozed_by(facts.user_id, today)
    # Waiting tasks and the member's snoozed ones are not "to do now": they leave every
    # number but their own (waiting first: a waiting task that is also snoozed is waiting).
    mine = whole & active & ~mine_snoozed
    numbers = {
        "mine": Count("id", filter=mine),
        "overdue": Count("id", filter=mine & Q(deadline__lt=today)),
        "within_week": Count(
            "id", filter=mine & Q(deadline__gte=today, deadline__lte=today + WEEK)
        ),
        "needs_date": Count("id", filter=mine & Q(deadline__isnull=True) & ~Q(term_quote="")),
        "waiting": Count("id", filter=whole & ~active),
        "snoozed": Count("id", filter=whole & active & mine_snoozed),
    }
    rows = whole
    if PARTS[(facts.split, Part.TO_PICK_UP)]:
        pick_up = _part_filter(Part.TO_PICK_UP, facts) or Q()
        numbers["to_pick_up"] = Count("id", filter=pick_up & active & ~mine_snoozed)
        rows = whole | pick_up
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
        waiting=int(counts["waiting"]),
        snoozed=int(counts["snoozed"]),
    )


def _with_people(tasks: list[Task]) -> list[Task]:
    """The names around the tasks (assignee, who gave it, who ticked it), fetched for the
    rows shown only: joined into the list query they cost a join per row before the sort
    (measured: ~120 ms at 20,000 tasks)."""
    prefetch_related_objects(tasks, "assigned_to", "assigned_by", "completed_by")
    return tasks


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


def _for_names(task: Task, names: dict[UUID, str], own: frozenset[UUID]) -> list[str]:
    """The letter's persons and departments other than the member's own persons: the
    member is "jou" ("Voor jou en Piet")."""
    if task.document is None:
        return []
    found = [names[p] for p in task.document.addressee_ids if p in names and p not in own]
    return sorted(found, key=str.casefold)

"""Giving a task to a member, and taking it back when sight of the letter goes (spec
"Mijn taken" S 5.3, S 15; ruling MT6, WZW P11).

One rule: a task on a letter is given only to a member who can read that letter, never
to a viewer (`documents.visibility.viewers`, the policy's own condition run for every
member). A task without a letter is not given to anyone (v1). When an assignee may no
longer read the letter, `release_unseeing_assignees` takes the assignment back as the
system (history origin `system`, no actor); every writer that narrows sight calls it
(`tests/test_task_release_callers.py`).

The check runs as the caller, the write as the system: once given, the row is still the
caller's to see (a task follows its letter), but the release must reach tasks the
current actor may not see. Check and write share the task's row lock.
"""

from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.documents import organise, visibility
from apps.documents.models import DocumentEvent
from apps.tasks.models import Task
from apps.tasks.services import TASK_RELATED, task_not_found
from apps.workspaces.context import system_actor
from apps.workspaces.models import Membership


@dataclass(frozen=True)
class Assignee:
    user_id: UUID
    name: str
    is_me: bool


def _visible(membership: Membership, task_id: UUID, *, lock: bool = False) -> Task:
    """The task as the member sees it (the task policy), not of a letter in the trash;
    404 like an unknown id otherwise."""
    tasks = Task.objects.filter(workspace_id=membership.workspace_id, id=task_id).filter(
        Q(document__isnull=True) | Q(document__trashed_at__isnull=True)
    )
    if lock:
        tasks = tasks.select_for_update(of=("self",))
    task = tasks.select_related(*TASK_RELATED).first()
    if task is None:
        raise task_not_found()
    return task


def _readers(workspace_id: UUID, document_id: UUID) -> dict[UUID, str]:
    """Members (not viewers) who can read the letter, with their names."""
    seen = visibility.viewers([document_id]).get(document_id, frozenset())
    if not seen:
        return {}
    return dict(
        Membership.objects.filter(workspace_id=workspace_id, user_id__in=seen)
        .exclude(role=Membership.Role.VIEWER)
        .values_list("user_id", "user__name")
    )


def assignees(membership: Membership, task_id: UUID) -> list[Assignee]:
    """Whom the caller may give the task to: the members who can read its letter, by
    name (a member list is a directory, S 4.10). Empty for a task without a letter."""
    task = _visible(membership, task_id)
    if task.document_id is None:
        return []
    readers = _readers(membership.workspace_id, task.document_id)
    return [
        Assignee(user_id=user_id, name=name, is_me=user_id == membership.user_id)
        for user_id, name in sorted(readers.items(), key=lambda pair: pair[1].casefold())
    ]


def assign(*, membership: Membership, user: User, task_id: UUID, assignee_id: UUID | None) -> Task:
    """Give the task to `assignee_id` ("Ik doe dit", "Geef aan…"), or take it back
    (`None`, "Niet meer aan mij"). The same assignee again writes nothing.

    404 `task_not_found` for a task the caller does not see; 422 `task_without_letter`;
    422 `assignee_cannot_read` for anyone who may not read the letter, a viewer, or no
    member.
    """
    with transaction.atomic():
        task = _visible(membership, task_id, lock=True)
        document = task.document
        if document is None:
            raise ApiError(
                422,
                "task_without_letter",
                _("Deze taak hoort niet bij een brief en kan niet worden gegeven."),
            )
        name = ""
        if assignee_id is not None:
            readers = _readers(membership.workspace_id, document.id)
            if assignee_id not in readers:
                raise ApiError(
                    422,
                    "assignee_cannot_read",
                    _("Deze persoon mag de brief niet lezen. Kies iemand anders."),
                )
            name = readers[assignee_id]
        if task.assigned_to_id == assignee_id:
            return task
        former = task.assigned_to.name if task.assigned_to is not None else ""
        task.assigned_to_id = assignee_id
        task.assigned_by = user if assignee_id is not None else None
        with system_actor():
            task.save(update_fields=["assigned_to", "assigned_by", "updated_at"])
        if assignee_id is not None:
            organise.record_task_event(
                document, DocumentEvent.Kind.TASK_ASSIGNED, user, person_name=name
            )
        else:
            organise.record_task_event(
                document, DocumentEvent.Kind.TASK_UNASSIGNED, user, person_name=former
            )
        return _visible(membership, task_id)


def release_unseeing_assignees(
    *, workspace_id: UUID, document_ids: Collection[UUID] | None = None
) -> int:
    """Take back every assignment (of those letters, or of the whole workspace) whose
    assignee may no longer read the letter or is now a viewer, as the system: event
    `task_unassigned`, origin `system`, no actor. The number released. Runs in the
    workspace's context; tasks without a letter are left alone (their assignee is
    what lets them be seen, R39)."""
    with transaction.atomic(), system_actor():
        tasks = Task.objects.filter(
            workspace_id=workspace_id, assigned_to__isnull=False, document__isnull=False
        )
        if document_ids is not None:
            tasks = tasks.filter(document_id__in=list(document_ids))
        rows = list(
            tasks.select_for_update(of=("self",))
            .select_related("document", "assigned_to")
            .order_by("id")
        )
        if not rows:
            return 0
        seen = visibility.viewers({t.document_id for t in rows if t.document_id is not None})
        viewers = set(
            Membership.objects.filter(
                workspace_id=workspace_id, role=Membership.Role.VIEWER
            ).values_list("user_id", flat=True)
        )
        released = 0
        for task in rows:
            document = task.document
            if document is None:
                continue
            if task.assigned_to_id in seen.get(document.id, frozenset()) and (
                task.assigned_to_id not in viewers
            ):
                continue
            name = task.assigned_to.name if task.assigned_to is not None else ""
            task.assigned_to = None
            task.assigned_by = None
            task.save(update_fields=["assigned_to", "assigned_by", "updated_at"])
            organise.record_task_event(
                document,
                DocumentEvent.Kind.TASK_UNASSIGNED,
                None,
                DocumentEvent.Origin.SYSTEM,
                person_name=name,
            )
            released += 1
        return released

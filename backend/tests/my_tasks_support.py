"""Worlds and helpers for the "Mijn taken" tests (plan Tasks 5 to 11).

`household()` is a Thuis workspace: admins Anne and Bas, member Piet, and Jan, a person
without an account. `office()` (visibility_support) is the organisation.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from apps.accounts.models import User
from apps.documents.models import Document, DocumentAddressee
from apps.people.models import Person
from apps.people.services import ensure_person_for_member
from apps.tasks import services
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory


@dataclass(frozen=True)
class Household:
    workspace: Workspace
    anne: User  # admin
    bas: User  # admin
    piet: User  # member
    persons: dict[str, Person]


def household() -> Household:
    anne = UserFactory.create(name="Anne")
    workspace = create_workspace(name="Thuis", kind="home", admin=anne)
    bas = UserFactory.create(name="Bas")
    piet = UserFactory.create(name="Piet")
    Membership.objects.create(user=bas, workspace=workspace, role=Membership.Role.ADMIN)
    Membership.objects.create(user=piet, workspace=workspace, role=Membership.Role.MEMBER)
    with workspace_context(workspace.id, actor=SYSTEM):
        persons = {
            user.name: ensure_person_for_member(workspace=workspace, user=user)
            for user in (anne, bas, piet)
        }
        persons["Jan"] = Person.objects.create(workspace=workspace, display_name="Jan")
    return Household(workspace=workspace, anne=anne, bas=bas, piet=piet, persons=persons)


def letter(
    workspace: Workspace,
    *,
    on: Sequence[Person] = (),
    description: str = "Betaal de rekening",
    deadline: date | None = None,
    term_quote: str = "",
    status: str = "done",
) -> Task:
    """A letter on `on` with one open task, written as the system."""
    with workspace_context(workspace.id, actor=SYSTEM):
        document = Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            status=status,
            addressee_ids=sorted(p.id for p in on),
        )
        for person in on:
            DocumentAddressee.objects.create(workspace=workspace, document=document, person=person)
        return Task.objects.create(
            workspace=workspace,
            document=document,
            type="pay",
            description=description,
            deadline=deadline,
            term_quote=term_quote,
        )


def membership(workspace: Workspace, user: User) -> Membership:
    return Membership.objects.select_related("workspace").get(workspace=workspace, user=user)


def mine(
    workspace: Workspace, user: User, *, part: str = "mine", status: str = "open"
) -> services.MyTaskList:
    with workspace_context(workspace.id, actor=user.id):
        return services.my_tasks(membership(workspace, user), part=part, status=status)


def counts(workspace: Workspace, user: User) -> services.MyTaskCounts:
    with workspace_context(workspace.id, actor=user.id):
        return services.count_my_tasks(membership(workspace, user))


def ids(result: services.MyTaskList) -> set[uuid.UUID]:
    return {item.task.id for item in result.items}


def task_of(workspace: Workspace, document: Document) -> Task:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Task.objects.get(document=document)

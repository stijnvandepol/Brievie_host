"""Letters with tasks for the letter status tests (spec "Briefstatus").

`letter_with_tasks` writes letters and tasks as the system and recomputes the cached facts
(`letter_sync.verify_workspace`, no history line), so a test starts from a consistent
world and changes it through the services.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from apps.documents.models import Document, DocumentAddressee, DocumentEvent
from apps.people.models import Person
from apps.tasks import letter_sync
from apps.tasks.letter_state import LetterFacts
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace


@dataclass(frozen=True)
class T:
    """One task of a test letter."""

    type: str = "reply"
    deadline: date | None = None
    waiting_until: date | None = None
    status: str = "open"
    description: str = "Doe iets"


def letter_with_tasks(
    workspace: Workspace,
    tasks: Sequence[T] = (),
    *,
    on: Sequence[Person] = (),
    status: str = "done",
) -> tuple[Document, list[Task]]:
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
        made = [
            Task.objects.create(
                workspace=workspace,
                document=document,
                type=t.type,
                description=t.description,
                deadline=t.deadline,
                waiting_until=t.waiting_until,
                status=t.status,
            )
            for t in tasks
        ]
        letter_sync.verify_workspace(workspace.id)
        document.refresh_from_db()
    return document, made


def stored(document_id: uuid.UUID, workspace: Workspace) -> LetterFacts:
    with workspace_context(workspace.id, actor=SYSTEM):
        document = Document.objects.get(id=document_id)
    return letter_sync.stored_facts(document)


def kinds(document_id: uuid.UUID, workspace: Workspace) -> list[str]:
    """The history kinds of the letter, oldest first."""
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(
            DocumentEvent.objects.filter(document_id=document_id)
            .order_by("created_at", "id")
            .values_list("kind", flat=True)
        )

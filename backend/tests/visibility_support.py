"""The office of the visibility tests (plan Task 7): one Organisatie workspace with every
kind of member and every kind of letter, and helpers to look at it as someone.

`office()` starts in state E off, D on, A off. Every letter has one row in every child
table (a page, an extraction, a label, an event, a suggestion, a task and a rule run),
and an addressee row for each addressee it is on; the private letter also has its share.
"""

import itertools
import uuid
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from apps.accounts.models import User
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentPage,
    DocumentReader,
    DocumentShare,
    DocumentSuggestion,
    Extraction,
)
from apps.labels.models import Label
from apps.people.models import DepartmentMember, Person
from apps.people.services import ensure_person_for_member
from apps.rules.models import Rule, RuleRun
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, Actor, workspace_context
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.rules_support import add_label, make_rule, text_word


def as_actor(workspace: Workspace, actor: Actor) -> AbstractContextManager[None]:
    return workspace_context(workspace.id, actor=actor)


def restrict_raw(document: Document, readers: Sequence[User]) -> None:
    """Make a letter private without the service (PB T3): reader rows with reason
    `share` for `readers`, then the flag, as the system."""
    with as_actor(document.workspace, SYSTEM):
        for user in readers:
            DocumentReader.objects.create(
                workspace_id=document.workspace_id,
                document=document,
                user=user,
                reason=DocumentReader.Reason.SHARE,
            )
        Document.objects.filter(id=document.id).update(visibility="restricted")


def set_state(
    workspace: Workspace, *, e: bool, d: bool, a: bool, sorters: Sequence[User] = ()
) -> None:
    """The three switches and the sorting flags, written directly (no service)."""
    Workspace.objects.filter(id=workspace.id).update(
        everyone_sees_all=e, departments_enabled=d, admins_read_all=a
    )
    Membership.objects.filter(workspace=workspace).update(sorts_post=False)
    Membership.objects.filter(workspace=workspace, user__in=list(sorters)).update(sorts_post=True)


# Every combination of the switches E, D, A and s (Sandra flagged, or nobody flagged).
ALL_16_STATES: Final[list[dict[str, bool]]] = [
    {"e": e, "d": d, "a": a, "s": s} for e, d, a, s in itertools.product((False, True), repeat=4)
]

LETTERS: Final = (
    "own_piet",
    "finance_letter",
    "hr_letter",
    "unassigned",
    "only_directie",
    "person_without_account",
    "scanned_in_review",
    "private_femke",
)


@dataclass(frozen=True)
class Office:
    workspace: Workspace
    admin: User  # Anne
    sorter: User  # Sandra, flagged
    finance: User  # Femke, in Finance
    hr: User  # Hans, in HR
    plain: User  # Piet
    viewer: User  # Vera, role viewer
    persons: dict[str, Person]
    letters: dict[str, Document]

    @property
    def users(self) -> dict[str, User]:
        return {
            "admin": self.admin,
            "sorter": self.sorter,
            "finance": self.finance,
            "hr": self.hr,
            "plain": self.plain,
            "viewer": self.viewer,
        }

    def state(self, *, e: bool, d: bool, a: bool, s: bool = True) -> None:
        set_state(self.workspace, e=e, d=d, a=a, sorters=[self.sorter] if s else [])

    def visible(self, actor: Actor) -> set[str]:
        """The names of the letters `actor` sees."""
        names = {letter.id: name for name, letter in self.letters.items()}
        with as_actor(self.workspace, actor):
            ids = set(Document.objects.values_list("id", flat=True))
        return {names[i] for i in ids if i in names}

    def name_of(self, document_id: uuid.UUID) -> str:
        return next(n for n, d in self.letters.items() if d.id == document_id)


def _member(workspace: Workspace, name: str, role: str = Membership.Role.MEMBER) -> User:
    user = UserFactory.create(name=name)
    Membership.objects.create(user=user, workspace=workspace, role=role)
    return user


def _letter(
    workspace: Workspace,
    *,
    on: Sequence[Person],
    status: str = "done",
    scanned_by: User | None = None,
    rule: Rule,
    label: Label,
) -> Document:
    document = Document.objects.create(
        workspace=workspace,
        client_id=uuid.uuid4(),
        page_count=1,
        status=status,
        scanned_by=scanned_by,
        addressee_ids=sorted(p.id for p in on),
    )
    for person in on:
        DocumentAddressee.objects.create(workspace=workspace, document=document, person=person)
    DocumentPage.objects.create(
        workspace=workspace,
        document=document,
        index=1,
        size=1,
        sha256="0" * 64,
        storage_path=f"test/{document.id}/1",
    )
    Extraction.objects.create(
        workspace=workspace,
        document=document,
        field=Extraction.Field.SENDER,
        value="Afzender",
        confidence=Decimal("0.900"),
    )
    DocumentLabel.objects.create(workspace=workspace, document=document, label=label)
    DocumentEvent.objects.create(
        workspace=workspace, document=document, kind=DocumentEvent.Kind.LABEL_ADDED
    )
    DocumentSuggestion.objects.create(
        workspace=workspace,
        document=document,
        action=DocumentSuggestion.Action.ADD_LABEL,
        label=label,
    )
    Task.objects.create(workspace=workspace, document=document, type="pay", description="Betalen")
    RuleRun.objects.create(
        workspace=workspace, rule=rule, document=document, trigger="manual", outcome="proposed"
    )
    return document


def office() -> Office:
    admin = UserFactory.create(name="Anne")
    workspace = create_workspace(name="Kantoor", kind="business", admin=admin)
    sorter = _member(workspace, "Sandra")
    finance = _member(workspace, "Femke")
    hr = _member(workspace, "Hans")
    plain = _member(workspace, "Piet")
    viewer = _member(workspace, "Vera", Membership.Role.VIEWER)
    with as_actor(workspace, SYSTEM):
        persons = {
            user.name: ensure_person_for_member(workspace=workspace, user=user)
            for user in (admin, sorter, finance, hr, plain, viewer)
        }
        persons["Jan"] = Person.objects.create(workspace=workspace, display_name="Jan")
        for name in ("Finance", "HR", "Directie"):
            persons[name] = Person.objects.create(
                workspace=workspace, display_name=name, kind=Person.Kind.DEPARTMENT
            )
        DepartmentMember.objects.create(
            workspace=workspace, department=persons["Finance"], user=finance
        )
        DepartmentMember.objects.create(workspace=workspace, department=persons["HR"], user=hr)
        label = Label.objects.filter(workspace=workspace).order_by("id").first()
        if label is None:
            label = Label.objects.create(workspace=workspace, name="Post")
        rule = make_rule(workspace, text_word("kantoor"), actions=[add_label(label)])
        letters = {
            "own_piet": _letter(workspace, on=[persons["Piet"]], rule=rule, label=label),
            "finance_letter": _letter(workspace, on=[persons["Finance"]], rule=rule, label=label),
            "hr_letter": _letter(workspace, on=[persons["HR"]], rule=rule, label=label),
            "unassigned": _letter(workspace, on=[], rule=rule, label=label),
            "only_directie": _letter(workspace, on=[persons["Directie"]], rule=rule, label=label),
            "person_without_account": _letter(
                workspace, on=[persons["Jan"]], rule=rule, label=label
            ),
            "scanned_in_review": _letter(
                workspace,
                on=[persons["Femke"]],
                status="needs_review",
                scanned_by=plain,
                rule=rule,
                label=label,
            ),
            "private_femke": _letter(workspace, on=[persons["Femke"]], rule=rule, label=label),
        }
        private = letters["private_femke"]
        DocumentShare.objects.create(workspace=workspace, document=private, user=finance)
    restrict_raw(private, [finance])
    with as_actor(workspace, SYSTEM):
        letters = {name: Document.objects.get(id=d.id) for name, d in letters.items()}
    result = Office(
        workspace=workspace,
        admin=admin,
        sorter=sorter,
        finance=finance,
        hr=hr,
        plain=plain,
        viewer=viewer,
        persons=persons,
        letters=letters,
    )
    result.state(e=False, d=True, a=False, s=True)
    return result

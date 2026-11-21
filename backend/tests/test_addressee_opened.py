"""Whether somebody a letter is addressed to opened it, never who (navigation plan Task 3,
spec S 5.5): the input of "Nog niet geopend"."""

import uuid

import pytest

from apps.documents import reading, services, visibility
from apps.documents.models import Document
from apps.documents.schemas import DocumentOut, DocumentSummaryOut
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.my_tasks_support import household, membership
from tests.visibility_support import as_actor, letter, office


def _opened(document: Document) -> bool:
    with as_actor(document.workspace, SYSTEM):
        return Document.objects.get(id=document.id).addressee_opened_at is not None


def _open(workspace: Workspace, user_id: uuid.UUID, document: Document) -> None:
    with as_actor(workspace, user_id):
        member = Membership.objects.select_related("workspace").get(
            workspace=workspace, user_id=user_id
        )
        reading.mark_read(member, document.id)


def test_addressee_opening_sets_opened() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    assert not _opened(document)
    _open(h.workspace, h.piet.id, document)
    assert _opened(document)


def test_manager_opening_sets_opened() -> None:
    h = household()
    with as_actor(h.workspace, SYSTEM):
        Person.objects.filter(id=h.persons["Jan"].id).update(managed_by=h.piet)
    document = letter(h.workspace, on=[h.persons["Jan"]])
    _open(h.workspace, h.piet.id, document)
    assert _opened(document)


def test_department_member_opening_sets_opened() -> None:
    o = office()
    document = o.letters["finance_letter"]
    _open(o.workspace, o.finance.id, document)
    assert _opened(document)


def test_scanner_or_sorter_opening_does_not_set_opened() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]], scanned_by=h.anne)
    _open(h.workspace, h.anne.id, document)
    _open(h.workspace, h.bas.id, document)
    assert not _opened(document)


def test_emergency_access_opening_does_not_set_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    monkeypatch.setattr(visibility, "only_through_emergency", lambda document: True)
    _open(h.workspace, h.piet.id, document)
    assert not _opened(document)


def test_new_addressee_without_mark_clears_opened() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    _open(h.workspace, h.piet.id, document)
    assert _opened(document)
    with as_actor(h.workspace, h.anne.id):
        services.set_document_addressees(
            workspace=h.workspace,
            user=h.anne,
            document_id=document.id,
            person_ids=[h.persons["Bas"].id],
        )
    assert not _opened(document)
    # Back to Piet, who had opened it: opened again.
    with as_actor(h.workspace, h.anne.id):
        services.set_document_addressees(
            workspace=h.workspace,
            user=h.anne,
            document_id=document.id,
            person_ids=[h.persons["Piet"].id],
        )
    assert _opened(document)


def test_the_same_addressees_again_change_nothing() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=document.id).update(addressee_opened_at=document.created_at)
    with as_actor(h.workspace, h.anne.id):
        services.set_document_addressees(
            workspace=h.workspace,
            user=h.anne,
            document_id=document.id,
            person_ids=[h.persons["Piet"].id],
        )
    assert _opened(document)


def test_mark_unread_keeps_opened() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    _open(h.workspace, h.piet.id, document)
    with as_actor(h.workspace, h.piet.id):
        reading.mark_unread(membership(h.workspace, h.piet), document.id)
    assert _opened(document)


def test_opened_is_not_in_any_response() -> None:
    for schema in (DocumentOut, DocumentSummaryOut):
        assert not any("opened" in name for name in schema.model_fields), schema

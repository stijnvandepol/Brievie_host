"""Whether somebody a letter is addressed to opened it, never who (navigation plan Task 3,
spec S 5.5): the input of "Nog niet geopend"."""

import uuid

import pytest
from django.test import Client

from apps.documents import reading, services, visibility
from apps.documents.models import Document
from apps.documents.schemas import DocumentOut, DocumentSummaryOut
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import jpeg, upload
from tests.my_tasks_support import household, membership
from tests.visibility_support import as_actor, home, letter, office, private_letter

JSON = "application/json"


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


# Security review of PR #44, point 1: only an opening counts, never a scan or emergency
# access, also when the scanner or emergency reader becomes an addressee later.


def test_a_scanner_who_becomes_the_addressee_has_not_opened(client: Client) -> None:
    h = household()
    client.force_login(h.piet)
    r = upload(client, h.workspace.id, [jpeg("scan")])
    assert r.status_code == 201
    with as_actor(h.workspace, SYSTEM):
        document = Document.objects.get(id=r.json()["id"])
        Document.objects.filter(id=document.id).update(status="done")
    with as_actor(h.workspace, h.anne.id):
        services.set_document_addressees(
            workspace=h.workspace,
            user=h.anne,
            document_id=document.id,
            person_ids=[h.persons["Piet"].id],
        )
    assert not _opened(document)
    # His own opening of the confirmed letter does count.
    _open(h.workspace, h.piet.id, document)
    assert _opened(document)


def test_an_emergency_reader_who_becomes_an_addressee_has_not_opened() -> None:
    v = home()
    Workspace.objects.filter(id=v.workspace.id).update(emergency_access=True)
    document = private_letter(v.workspace, addressees=[v.persons["Bob"]])
    anne = Client()
    anne.force_login(v.anne)
    r = anne.post(
        f"/api/v1/workspaces/{v.workspace.id}/persons/{v.persons['Bob'].id}/emergency-access",
        {"reason": "Bob ligt in het ziekenhuis"},
        content_type=JSON,
    )
    assert r.json()["opened_count"] == 1
    r = anne.post(
        f"/api/v1/workspaces/{v.workspace.id}/documents/{document.id}/read", content_type=JSON
    )
    assert r.status_code == 204
    # Bob puts Anne on the letter too: her emergency opening is no addressee's opening.
    with as_actor(v.workspace, v.bob.id):
        services.set_document_addressees(
            workspace=v.workspace,
            user=v.bob,
            document_id=document.id,
            person_ids=[v.persons["Bob"].id, v.persons["Anne"].id],
        )
    assert not _opened(document)


# Point 3: only an opening of a confirmed letter counts; confirming recomputes.


def test_opening_during_review_does_not_count_until_opened_after_confirm(
    client: Client,
) -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]], status="needs_review")
    _open(h.workspace, h.piet.id, document)
    assert not _opened(document)
    client.force_login(h.anne)
    r = client.post(
        f"/api/v1/workspaces/{h.workspace.id}/documents/{document.id}/confirm",
        {
            "sender": "Gemeente",
            "document_type": "",
            "summary": "",
            "summary_by_ai": False,
            "tasks": [],
        },
        content_type=JSON,
    )
    assert r.status_code == 200, r.json()
    assert not _opened(document)
    _open(h.workspace, h.piet.id, document)
    assert _opened(document)


def test_confirming_keeps_an_earlier_opening_of_the_confirmed_letter(client: Client) -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    _open(h.workspace, h.piet.id, document)
    # Back in review (as a reopen leaves it), then confirmed again.
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=document.id).update(status="needs_review")
    client.force_login(h.anne)
    r = client.post(
        f"/api/v1/workspaces/{h.workspace.id}/documents/{document.id}/confirm",
        {
            "sender": "Gemeente",
            "document_type": "",
            "summary": "",
            "summary_by_ai": False,
            "tasks": [],
        },
        content_type=JSON,
    )
    assert r.status_code == 200, r.json()
    assert _opened(document)


def test_mark_all_read_is_no_opening() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    with as_actor(h.workspace, h.piet.id):
        reading.mark_many_read(membership(h.workspace, h.piet), [document.id])
    assert not _opened(document)

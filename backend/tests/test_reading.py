"""Marking a letter read or unread, for yourself only (navigation plan Task 2, spec S 5)."""

import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.common.errors import ApiError
from apps.documents import reading, services
from apps.documents.models import Document, DocumentEvent, DocumentReadMark
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.documents_support import jpeg, trashed, upload
from tests.my_tasks_support import Household, household, membership
from tests.visibility_support import as_actor, letter, office


def _unread(h: Household, user_id: uuid.UUID) -> set[uuid.UUID]:
    with as_actor(h.workspace, user_id):
        member = Membership.objects.select_related("workspace").get(
            workspace=h.workspace, user_id=user_id
        )
        rows = services.list_documents(member)
        return reading.unread_ids(member, rows)


def _mark(h: Household, user_id: uuid.UUID, document: Document) -> None:
    with as_actor(h.workspace, user_id):
        member = Membership.objects.select_related("workspace").get(
            workspace=h.workspace, user_id=user_id
        )
        reading.mark_read(member, document.id)


def _marks(workspace_id: uuid.UUID, document: Document) -> list[uuid.UUID]:
    with as_actor(document.workspace, SYSTEM):
        return list(
            DocumentReadMark.objects.filter(document=document).values_list("user_id", flat=True)
        )


def test_mark_read_then_not_unread() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    assert document.id in _unread(h, h.piet.id)
    _mark(h, h.piet.id, document)
    assert document.id not in _unread(h, h.piet.id)
    # Another member's state is their own.
    assert document.id in _unread(h, h.anne.id)


def test_mark_read_twice_is_one_row() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    _mark(h, h.piet.id, document)
    _mark(h, h.piet.id, document)
    assert _marks(h.workspace.id, document) == [h.piet.id]


def test_mark_read_on_invisible_letter_is_404_and_writes_nothing() -> None:
    o = office()
    hidden = o.letters["finance_letter"]
    member = Membership.objects.select_related("workspace").get(workspace=o.workspace, user=o.hr)
    with as_actor(o.workspace, o.hr.id), pytest.raises(ApiError) as raised:
        reading.mark_read(member, hidden.id)
    assert (raised.value.status, raised.value.code) == (404, "document_not_found")
    assert _marks(o.workspace.id, hidden) == []


def test_mark_read_on_trashed_letter_answers_document_in_trash() -> None:
    h = household()
    document = trashed(letter(h.workspace, on=[h.persons["Piet"]]))
    member = membership(h.workspace, h.piet)
    with as_actor(h.workspace, h.piet.id), pytest.raises(ApiError) as raised:
        reading.mark_read(member, document.id)
    assert raised.value.code == "document_in_trash"


def test_mark_unread_removes_only_own_mark() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    _mark(h, h.piet.id, document)
    _mark(h, h.anne.id, document)
    with as_actor(h.workspace, h.piet.id):
        reading.mark_unread(membership(h.workspace, h.piet), document.id)
    assert _marks(h.workspace.id, document) == [h.anne.id]
    assert document.id in _unread(h, h.piet.id)


def test_processing_letter_is_never_unread() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]], status="processing")
    assert document.id not in _unread(h, h.piet.id)


def test_letter_before_unread_from_is_read() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    Membership.objects.filter(workspace=h.workspace, user=h.piet).update(
        unread_from=timezone.now() + timedelta(seconds=1)
    )
    assert document.id not in _unread(h, h.piet.id)


def test_uploader_has_read_own_upload(client: Client) -> None:
    h = household()
    client.force_login(h.anne)
    r = upload(client, h.workspace.id, [jpeg("een")])
    assert r.status_code == 201
    with as_actor(h.workspace, SYSTEM):
        document = Document.objects.get(id=r.json()["id"])
    assert _marks(h.workspace.id, document) == [h.anne.id]


def test_mark_many_skips_invisible_ids_and_caps_at_200() -> None:
    o = office()
    member = Membership.objects.select_related("workspace").get(workspace=o.workspace, user=o.hr)
    own = o.letters["hr_letter"]
    hidden = o.letters["finance_letter"]
    with as_actor(o.workspace, o.hr.id):
        assert reading.mark_many_read(member, [own.id, hidden.id, uuid.uuid4()]) == 1
        assert reading.mark_many_read(member, [own.id]) == 0
        with pytest.raises(ApiError) as raised:
            reading.mark_many_read(member, [uuid.uuid4() for _ in range(201)])
    assert (raised.value.status, raised.value.code) == (422, "validation_error")
    assert _marks(o.workspace.id, own) == [o.hr.id]
    assert _marks(o.workspace.id, hidden) == []


def test_mark_writes_no_event_or_audit() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])

    def counted() -> tuple[int, int, int]:
        with as_actor(h.workspace, SYSTEM):
            return (
                DocumentEvent.objects.count(),
                WorkspaceAuditEvent.objects.count(),
                ServerAuditEvent.objects.count(),
            )

    before = counted()
    _mark(h, h.piet.id, document)
    with as_actor(h.workspace, h.piet.id):
        reading.mark_unread(membership(h.workspace, h.piet), document.id)
        reading.mark_many_read(membership(h.workspace, h.piet), [document.id])
    assert counted() == before


def test_get_document_does_not_mark(client: Client) -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    client.force_login(h.piet)
    r = client.get(f"/api/v1/workspaces/{h.workspace.id}/documents/{document.id}")
    assert r.status_code == 200
    assert _marks(h.workspace.id, document) == []


def test_leaving_member_marks_go() -> None:
    from apps.documents import visibility

    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    _mark(h, h.piet.id, document)
    _mark(h, h.anne.id, document)
    with as_actor(h.workspace, h.anne.id):
        visibility.forget_member(h.workspace.id, h.piet.id)
    assert _marks(h.workspace.id, document) == [h.anne.id]

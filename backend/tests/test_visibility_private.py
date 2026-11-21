"""Private letters, making them public again, and shares on every letter (plan Task 24,
PB T7, S 4.8)."""

import pytest
from django.test import Client

from apps.accounts.models import AccountNotice
from apps.audit.models import WorkspaceAuditEvent
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.documents import visibility
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentShare,
)
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.factories import UserFactory
from tests.visibility_support import (
    as_actor,
    home,
    letter,
    office,
    private_letter,
    readers,
    sees,
)


def _events(document: Document, kind: str) -> list[DocumentEvent]:
    with as_actor(document.workspace, SYSTEM):
        return list(DocumentEvent.objects.filter(document=document, kind=kind))


def _audit(document: Document) -> list[WorkspaceAuditEvent]:
    with as_actor(document.workspace, SYSTEM):
        return list(
            WorkspaceAuditEvent.objects.filter(
                code="visibility_changed", target_id=document.id
            ).order_by("created_at", "id")
        )


def _visibility(document: Document) -> str:
    with as_actor(document.workspace, SYSTEM):
        return Document.objects.get(id=document.id).visibility


def test_make_private_gives_scanner_and_actor_a_share() -> None:
    h = home()
    doc = letter(h.workspace, scanned_by=h.bob)
    with as_actor(h.workspace, h.anne.id):
        result = visibility.make_private(doc, actor=AuditActor.for_user(h.anne), origin="user")
    assert result == "made_private"
    assert readers(doc) == {("Bob", "share"), ("Anne", "share")}
    assert len(_events(doc, "made_private")) == 1
    assert not sees(h.carla, doc)


def test_make_private_writes_history_before_the_flip() -> None:
    h = home()
    doc = letter(h.workspace, scanned_by=h.bob)
    with as_actor(h.workspace, h.anne.id):
        visibility.make_private(doc, actor=AuditActor.for_user(h.anne), origin="user")
        events = list(DocumentEvent.objects.filter(document=doc, kind="made_private"))
    assert [e.actor_id for e in events] == [h.anne.id]


def test_make_private_audits_visibility_changed_with_the_id_only() -> None:
    h = home()
    doc = letter(h.workspace, scanned_by=h.bob)
    with as_actor(h.workspace, h.anne.id):
        visibility.make_private(doc, actor=AuditActor.for_user(h.anne), origin="user")
    (row,) = _audit(doc)
    assert row.target_name == ""
    assert row.values == {"visibility": "restricted", "origin": "user"}


def test_readers_after_visibility_switched_twice() -> None:
    h = home()
    doc = letter(h.workspace, scanned_by=h.bob)
    anne = AuditActor.for_user(h.anne)
    with as_actor(h.workspace, h.anne.id):
        visibility.make_private(doc, actor=anne, origin="user", share_user_ids=[h.carla.id])
    assert ("Carla", "share") in readers(doc)
    with as_actor(h.workspace, h.anne.id):
        assert visibility.make_public(doc, actor=anne, user_id=h.anne.id)
        assert visibility.make_private(doc, actor=anne, origin="user") == "made_private"
    assert readers(doc) == {("Bob", "share"), ("Anne", "share")}


def test_make_public_by_addressee() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    with as_actor(h.workspace, h.bob.id):
        assert visibility.make_public(doc, actor=AuditActor.for_user(h.bob), user_id=h.bob.id)
    assert _visibility(doc) == "workspace"
    assert readers(doc) == set()
    assert sees(h.carla, doc)


def test_make_public_by_non_addressee_when_addressee_has_account_is_403() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]], shared_with=[h.carla])
    with as_actor(h.workspace, h.carla.id), pytest.raises(ApiError) as caught:
        visibility.make_public(doc, actor=AuditActor.for_user(h.carla), user_id=h.carla.id)
    assert caught.value.status == 403
    assert caught.value.code == "cannot_make_public"
    assert _visibility(doc) == "restricted"


def test_any_reader_may_make_public_when_no_addressee_has_account() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.carla)
    with as_actor(h.workspace, h.carla.id):
        assert visibility.make_public(doc, actor=AuditActor.for_user(h.carla), user_id=h.carla.id)
    assert _visibility(doc) == "workspace"


def _url(doc: Document, tail: str) -> str:
    return f"/api/v1/workspaces/{doc.workspace_id}/documents/{doc.id}/{tail}"


def test_removing_own_last_access_by_organising_is_409_would_lose_access(client: Client) -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    client.force_login(h.bob)
    r = client.put(_url(doc, "addressees"), {"person_ids": []}, content_type="application/json")
    assert r.status_code == 409, r.json()
    assert r.json()["code"] == "would_lose_access"
    with as_actor(h.workspace, SYSTEM):
        assert DocumentAddressee.objects.filter(document=doc).count() == 1
    assert readers(doc) == {("Bob", "addressee")}


def test_undo_that_removes_own_access_is_409(client: Client) -> None:
    h = home()
    doc = letter(h.workspace, on=[h.persons["Bob"]])
    with as_actor(h.workspace, SYSTEM):
        event = DocumentEvent.objects.create(
            workspace=h.workspace,
            document=doc,
            kind=DocumentEvent.Kind.ADDRESSEE_ADDED,
            origin=DocumentEvent.Origin.RULE,
            person=h.persons["Bob"],
            person_name="Bob",
        )
        visibility.make_private(doc, actor=AuditActor.system(), origin="user")
    client.force_login(h.bob)
    r = client.post(_url(doc, f"events/{event.id}/undo"), content_type="application/json")
    assert r.status_code == 409, r.json()
    assert r.json()["code"] == "would_lose_access"
    assert readers(doc) == {("Bob", "addressee")}


def test_set_shares_may_remove_own_share() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.carla)
    with as_actor(h.workspace, h.carla.id):
        visibility.set_shares(doc, user_ids=[], actor=AuditActor.for_user(h.carla))
        assert not visibility.can_read(doc.id)


def test_share_change_is_audited_as_visibility_changed() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    bob = AuditActor.for_user(h.bob)
    with as_actor(h.workspace, h.bob.id):
        visibility.set_shares(doc, user_ids=[h.carla.id], actor=bob)
        visibility.set_shares(doc, user_ids=[], actor=bob)
    rows = [r.values for r in _audit(doc) if "share" in r.values]
    assert rows == [
        {"visibility": "restricted", "share": "added", "member": "Carla"},
        {"visibility": "restricted", "share": "removed", "member": "Carla"},
    ]
    assert [e.person_name for e in _events(doc, "share_added")] == ["Carla"]
    assert [e.person_name for e in _events(doc, "share_removed")] == ["Carla"]


def test_automatic_make_private_without_any_reader_keeps_the_letter_public() -> None:
    h = home()
    gone = UserFactory.create(name="Gerrit")
    Membership.objects.create(user=gone, workspace=h.workspace, role=Membership.Role.MEMBER)
    doc = letter(h.workspace, on=[h.persons["Oma"]], scanned_by=gone)
    Membership.objects.filter(user=gone, workspace=h.workspace).delete()
    with as_actor(h.workspace, SYSTEM):
        result = visibility.make_private(doc, actor=AuditActor.system(), origin="rule")
        assert not DocumentShare.objects.filter(document=doc).exists()
    assert result == "nobody_could_read"
    assert _visibility(doc) == "workspace"
    assert len(_events(doc, "private_skipped")) == 1
    notices = list(AccountNotice.objects.filter(user=h.anne, code="private_without_readers"))
    assert [n.values["document_id"] for n in notices] == [str(doc.id)]


def test_scanner_absent_and_no_addressee_also_keeps_it_public() -> None:
    h = home()
    doc = letter(h.workspace)
    with as_actor(h.workspace, SYSTEM):
        result = visibility.make_private(doc, actor=AuditActor.system(), origin="rule")
    assert result == "nobody_could_read"
    assert _visibility(doc) == "workspace"


def test_the_admin_notice_is_sent_once_per_document() -> None:
    h = home()
    doc = letter(h.workspace)
    with as_actor(h.workspace, SYSTEM):
        visibility.make_private(doc, actor=AuditActor.system(), origin="rule")
        visibility.make_private(doc, actor=AuditActor.system(), origin="rule")
    assert len(_events(doc, "private_skipped")) == 2
    assert AccountNotice.objects.filter(user=h.anne, code="private_without_readers").count() == 1


def test_already_private_changes_nothing() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    with as_actor(h.workspace, SYSTEM):
        result = visibility.make_private(doc, actor=AuditActor.system(), origin="user")
    assert result == "already_private"
    assert len(_events(doc, "made_private")) == 1


def test_share_with_a_non_member_is_404() -> None:
    h = home()
    stranger = UserFactory.create()
    doc = letter(h.workspace, scanned_by=h.bob)
    with as_actor(h.workspace, h.bob.id), pytest.raises(ApiError) as caught:
        visibility.set_shares(doc, user_ids=[stranger.id], actor=AuditActor.for_user(h.bob))
    assert caught.value.code == "member_not_found"


# Shares on every letter (R44), in the office: "Iedereen ziet alle post" is off.


def test_share_on_a_non_private_letter_lets_piet_read_it() -> None:
    o = office()
    doc = o.letters["finance_letter"]
    assert "finance_letter" not in o.visible(o.plain.id)
    with as_actor(o.workspace, o.finance.id):
        visibility.set_shares(doc, user_ids=[o.plain.id], actor=AuditActor.for_user(o.finance))
    assert "finance_letter" in o.visible(o.plain.id)
    assert _visibility(doc) == "workspace"
    (row,) = [r.values for r in _audit(doc)]
    assert row == {"visibility": "workspace", "share": "added", "member": "Piet"}


def test_removing_the_share_hides_it_again() -> None:
    o = office()
    doc = o.letters["finance_letter"]
    femke = AuditActor.for_user(o.finance)
    with as_actor(o.workspace, o.finance.id):
        visibility.set_shares(doc, user_ids=[o.plain.id], actor=femke)
        visibility.set_shares(doc, user_ids=[], actor=femke)
    assert "finance_letter" not in o.visible(o.plain.id)


def test_losing_a_non_private_letter_by_organising_is_not_409(client: Client) -> None:
    o = office()
    doc = o.letters["finance_letter"]
    client.force_login(o.finance)
    r = client.put(
        _url(doc, "addressees"),
        {"person_ids": [str(o.persons["HR"].id)]},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    assert r.json()["can_read"] is False

"""The full export, version 2 (archive plan Task 10, spec S 9, AR10, AR13): a PDF per
document, own documents and their fields, the masked text; a member's own post, and an
admin's everything they may read, provably without what they may not read."""

import io
import zipfile
from datetime import date, timedelta
from uuid import UUID

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.documents import export, member_export
from apps.documents.models import Document, DocumentReader, MemberExport
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.archive_support import membership_of
from tests.documents_support import bearer_for, jpeg
from tests.export_support import export_documents, read_zip, sealed_document
from tests.helpers import post_json
from tests.visibility_support import Office, as_actor, office, restrict_raw


def _own(ws: Workspace, user: User, **fields: object) -> Document:
    """A sealed document turned into an own document, as the system."""
    document, _pages = sealed_document(ws, user, pages=2)
    with as_actor(ws, SYSTEM):
        Document.objects.filter(id=document.id).update(
            kind="document", title="Garantiebewijs wasmachine", document_type="warranty", **fields
        )
        return Document.objects.get(id=document.id)


def _zip(ws: Workspace, ids: list[UUID], reader: UUID | None) -> zipfile.ZipFile:
    target = io.BytesIO()
    export.write_export(
        workspace_id=ws.id, reader_id=reader, document_ids=ids, target=target, title="Archief"
    )
    return read_zip(target.getvalue())


def _admin_ids(o: Office) -> set[str]:
    return {
        o.name_of(i)
        for i in member_export.admin_document_ids(o.workspace.id, o.admin.id)
        if i in {d.id for d in o.letters.values()}
    }


def test_export_v2_json_has_the_documented_fields_and_both_kinds(ws_a: Workspace) -> None:
    admin = Membership.objects.get(workspace=ws_a, role="admin").user
    letter, _pages = sealed_document(ws_a, admin, pages=1, sender="Waterbedrijf")
    own = _own(
        ws_a,
        admin,
        expires_on=date(2031, 3, 12),
        retain_until=date(2033, 1, 1),
        letter_date=date(2026, 2, 1),
    )
    archive = _zip(ws_a, [letter.id, own.id], None)
    entries = {e["id"]: e for e in export_documents(archive)}
    entry = entries[str(own.id)]
    for key in (
        "id", "kind", "title", "sender", "document_type", "document_date", "expires_on",
        "retain_until", "summary", "ocr_text", "source", "created_at", "status", "persons",
        "people", "labels", "tasks", "history", "pdf", "original", "pages", "letter_date",
        "summary_by_ai",
    ):  # fmt: skip
        assert key in entry, key
    assert (entry["kind"], entry["title"], entry["document_type"]) == (
        "document",
        "Garantiebewijs wasmachine",
        "warranty",
    )
    assert (entry["document_date"], entry["expires_on"], entry["retain_until"]) == (
        "2026-02-01",
        "2031-03-12",
        "2033-01-01",
    )
    assert entry["original"] is None
    assert (
        entry["pdf"]
        == "documents/2026/2026-02-01_garantiebewijs-wasmachine_" + str(own.id)[:8] + ".pdf"
    )
    assert entries[str(letter.id)]["kind"] == "letter"
    pdf = archive.read(str(entry["pdf"]))
    assert pdf.startswith(b"%PDF-1.4")
    assert b"/Count 2" in pdf
    assert archive.read(str(entries[str(letter.id)]["pdf"])).startswith(b"%PDF")


def test_v1_readers_still_find_their_fields(ws_a: Workspace) -> None:
    admin = Membership.objects.get(workspace=ws_a, role="admin").user
    letter, _pages = sealed_document(ws_a, admin, pages=1)
    [entry] = export_documents(_zip(ws_a, [letter.id], None))
    for key in (
        "id", "status", "sender", "document_type", "letter_date", "summary", "summary_by_ai",
        "created_at", "persons", "labels", "tasks", "pages",
    ):  # fmt: skip
        assert key in entry, key
    assert entry["persons"] == []


def test_text_in_the_export_is_the_masked_text(ws_a: Workspace) -> None:
    admin = Membership.objects.get(workspace=ws_a, role="admin").user
    letter, _pages = sealed_document(ws_a, admin, pages=1)
    with as_actor(ws_a, SYSTEM):
        Document.objects.filter(id=letter.id).update(ocr_text="Uw BSN [BSN] staat hier")
    [entry] = export_documents(_zip(ws_a, [letter.id], None))
    assert entry["ocr_text"] == "Uw BSN [BSN] staat hier"


def test_admin_export_excludes_private_letters_the_admin_cannot_read() -> None:
    o = office()
    o.state(e=False, d=True, a=False)
    seen = _admin_ids(o)
    assert "private_femke" not in seen
    assert seen == o.visible(o.admin.id) - {"private_femke"}
    # Admins who read all post read every letter but the private ones not theirs.
    o.state(e=False, d=True, a=True)
    seen = _admin_ids(o)
    assert "private_femke" not in seen
    assert "finance_letter" in seen
    # Piet's letter made private for Femke only is no longer the admin's to export either.
    restrict_raw(o.letters["own_piet"], [o.finance])
    assert "own_piet" not in _admin_ids(o)


def test_admin_export_leaves_out_what_only_emergency_access_opens() -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        DocumentReader.objects.create(
            workspace=o.workspace,
            document=o.letters["private_femke"],
            user=o.admin,
            reason=DocumentReader.Reason.EMERGENCY,
            expires_at=timezone.now() + timedelta(hours=1),
        )
    assert "private_femke" in o.visible(o.admin.id)
    assert "private_femke" not in _admin_ids(o)


def test_all_scope_is_session_only_and_admin_only(client: Client) -> None:
    o = office()
    url = f"/workspaces/{o.workspace.id}/my-export?scope=all"
    device = Client()
    response = device.post(
        f"/api/v1{url}", content_type="application/json", headers=bearer_for(o.admin)
    )
    assert response.status_code == 401
    client.force_login(o.plain)
    response = post_json(client, url)
    assert (response.status_code, response.json()["code"]) == (403, "not_admin")
    client.force_login(o.admin)
    response = post_json(client, url)
    assert response.status_code == 202, response.json()
    assert response.json()["scope"] == "all"
    assert post_json(client, f"/workspaces/{o.workspace.id}/my-export?scope=x").status_code in (
        409,
        422,
    )


def test_mine_obeys_allow_member_export_and_all_does_not(client: Client) -> None:
    o = office()
    Workspace.objects.filter(id=o.workspace.id).update(allow_member_export=False)
    client.force_login(o.admin)
    mine = post_json(client, f"/workspaces/{o.workspace.id}/my-export")
    assert (mine.status_code, mine.json()["code"]) == (403, "export_not_allowed")
    everything = post_json(client, f"/workspaces/{o.workspace.id}/my-export?scope=all")
    assert everything.status_code == 202


def test_the_admins_built_export_holds_only_what_the_admin_may_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = office()
    member = membership_of(o.workspace, o.admin)
    # The office's page rows have no files: every page reads as one small picture.
    monkeypatch.setattr(export, "_open_page", lambda *args, **kwargs: jpeg())
    written: list[list[UUID]] = []
    real = export.write_export

    def spy(**kwargs: object) -> export.ExportCounts:
        ids = kwargs["document_ids"]
        assert isinstance(ids, list)
        written.append(ids)
        return real(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(member_export, "write_export", spy)
    with as_actor(o.workspace, o.admin.id):
        row = member_export.create_my_export(
            membership=member, actor=AuditActor.for_user(o.admin), scope="all"
        )
    member_export.build_export(o.workspace.id, row.id)
    with as_actor(o.workspace, SYSTEM):
        stored = MemberExport.objects.get(id=row.id)
    assert stored.status == MemberExport.Status.READY
    [ids] = written
    assert {o.name_of(i) for i in ids} == _admin_ids(o)
    assert o.letters["private_femke"].id not in ids
    assert stored.document_count == len(ids) >= 1


def test_a_demoted_admin_cannot_ask_for_everything() -> None:
    o = office()
    member = membership_of(o.workspace, o.admin)
    Membership.objects.filter(workspace=o.workspace, user=o.admin).update(
        role=Membership.Role.MEMBER
    )
    with as_actor(o.workspace, o.admin.id), pytest.raises(ApiError) as caught:
        member_export.create_my_export(
            membership=member, actor=AuditActor.for_user(o.admin), scope="all"
        )
    assert (caught.value.status, caught.value.code) == (403, "not_admin")

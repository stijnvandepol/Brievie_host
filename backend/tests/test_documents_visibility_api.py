"""Private by hand and by the Zorg label; shares on every letter (plan Task 30, PB T17,
R44)."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

from django.test import Client

from apps.accounts.models import AccountNotice
from apps.audit.services import AuditActor
from apps.documents import organise, visibility
from apps.documents.models import Document, DocumentEvent, DocumentSuggestion
from apps.labels.models import Label
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory
from tests.visibility_support import as_actor, home, letter, office, private_letter, readers, sees

if TYPE_CHECKING:
    from tests.helpers import Response

JSON = "application/json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _zorg(workspace: Workspace) -> Label:
    with as_actor(workspace, SYSTEM):
        label, _created = Label.objects.get_or_create(
            workspace=workspace, system_key="zorg", defaults={"name": "Zorg"}
        )
        return label


def _url(doc: Document, tail: str = "") -> str:
    return f"/api/v1/workspaces/{doc.workspace_id}/documents/{doc.id}{tail}"


def _visibility(doc: Document) -> str:
    with as_actor(doc.workspace, SYSTEM):
        return Document.objects.get(id=doc.id).visibility


def _set(client: Client, doc: Document, value: str, shared: tuple[object, ...] = ()) -> "Response":
    return client.put(
        _url(doc, "/visibility"),
        {"visibility": value, "shared_user_ids": [str(u) for u in shared]},
        content_type=JSON,
    )


def test_zorg_label_by_hand_makes_the_letter_private_for_actor_and_scanner(
    client: Client,
) -> None:
    h = home()
    doc = letter(h.workspace, scanned_by=h.bob)
    client.force_login(h.carla)
    r = client.put(_url(doc, "/labels"), {"label_ids": [str(_zorg(h.workspace).id)]}, JSON)
    assert r.status_code == 200, r.json()
    assert r.json()["visibility"] == "restricted"
    assert readers(doc) == {("Carla", "share"), ("Bob", "share")}
    assert not sees(h.anne, doc)


def test_zorg_label_by_accepting_a_suggestion_makes_it_private(client: Client) -> None:
    h = home()
    doc = letter(h.workspace)
    with as_actor(h.workspace, SYSTEM):
        suggestion = DocumentSuggestion.objects.create(
            workspace=h.workspace,
            document=doc,
            action=DocumentSuggestion.Action.ADD_LABEL,
            label=_zorg(h.workspace),
            origin=DocumentSuggestion.Origin.AI,
        )
    client.force_login(h.bob)
    r = client.post(_url(doc, f"/suggestions/{suggestion.id}/accept"), content_type=JSON)
    assert r.status_code == 200, r.json()
    assert _visibility(doc) == "restricted"
    assert readers(doc) == {("Bob", "share")}


def test_zorg_suggestion_alone_does_not() -> None:
    h = home()
    doc = letter(h.workspace)
    with as_actor(h.workspace, SYSTEM):
        DocumentSuggestion.objects.create(
            workspace=h.workspace,
            document=doc,
            action=DocumentSuggestion.Action.ADD_LABEL,
            label=_zorg(h.workspace),
            origin=DocumentSuggestion.Origin.AI,
        )
    assert _visibility(doc) == "workspace"


def test_zorg_label_in_a_business_workspace_stays_public(client: Client) -> None:
    o = office()
    doc = o.letters["finance_letter"]
    client.force_login(o.finance)
    r = client.put(_url(doc, "/labels"), {"label_ids": [str(_zorg(o.workspace).id)]}, JSON)
    assert r.status_code == 200
    assert _visibility(doc) == "workspace"


def test_zorg_label_by_a_pipeline_rule_without_any_reader_stays_public() -> None:
    h = home()
    gone = UserFactory.create()
    Membership.objects.create(user=gone, workspace=h.workspace, role=Membership.Role.MEMBER)
    doc = letter(h.workspace, scanned_by=gone)
    Membership.objects.filter(user=gone).delete()
    with as_actor(h.workspace, SYSTEM):
        organise.add_label(doc, _zorg(h.workspace), actor=None, origin="rule")
        assert DocumentEvent.objects.filter(document=doc, kind="private_skipped").count() == 1
    assert _visibility(doc) == "workspace"
    assert AccountNotice.objects.filter(user=h.anne, code="private_without_readers").exists()


def test_set_visibility_restricted_then_lists_show_lock_fields(client: Client) -> None:
    h = home()
    doc = letter(h.workspace, scanned_by=h.bob)
    client.force_login(h.anne)
    r = _set(client, doc, "restricted")
    assert r.status_code == 200, r.json()
    assert r.json() == {
        "visibility": "restricted",
        "reader_names": ["Anne", "Bob"],
        "shared_user_ids": sorted([str(h.anne.id), str(h.bob.id)]),
        "can_read": True,
    }
    (row,) = [d for d in client.get(f"/api/v1/workspaces/{h.workspace.id}/documents").json()]
    assert (row["visibility"], row["reader_names"]) == ("restricted", ["Anne", "Bob"])
    detail = client.get(_url(doc)).json()
    assert detail["can_make_public"] is True
    assert sorted(detail["shared_user_ids"]) == sorted([str(h.anne.id), str(h.bob.id)])


def test_set_visibility_shares_with_a_member(client: Client) -> None:
    h = home()
    doc = letter(h.workspace, scanned_by=h.bob)
    client.force_login(h.bob)
    assert _set(client, doc, "restricted", (h.carla.id,)).status_code == 200
    assert sees(h.carla, doc)
    assert not sees(h.anne, doc)


def test_set_visibility_share_with_non_member_is_404(client: Client) -> None:
    h = home()
    doc = letter(h.workspace, scanned_by=h.bob)
    client.force_login(h.bob)
    r = _set(client, doc, "restricted", (UserFactory.create().id,))
    assert (r.status_code, r.json()["code"]) == (404, "member_not_found")
    assert _visibility(doc) == "workspace"


def test_removing_own_share_answers_can_read_false(client: Client) -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.carla)
    client.force_login(h.carla)
    r = _set(client, doc, "restricted")
    assert r.status_code == 200
    assert r.json()["can_read"] is False
    assert r.json()["reader_names"] == []


def test_make_public_by_non_addressee_is_403_and_nothing_changes(client: Client) -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]], shared_with=[h.carla])
    client.force_login(h.carla)
    r = _set(client, doc, "workspace")
    assert (r.status_code, r.json()["code"]) == (403, "cannot_make_public")
    assert _visibility(doc) == "restricted"
    assert ("Carla", "share") in readers(doc)


def test_viewer_cannot_change_visibility(client: Client) -> None:
    h = home()
    Membership.objects.filter(user=h.carla).update(role=Membership.Role.VIEWER)
    doc = letter(h.workspace)
    client.force_login(h.carla)
    r = _set(client, doc, "restricted")
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")


def test_an_unknown_visibility_is_422(client: Client) -> None:
    h = home()
    doc = letter(h.workspace)
    client.force_login(h.bob)
    assert _set(client, doc, "geheim").status_code == 422


def test_non_reader_gets_404_for_the_letter(client: Client) -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    client.force_login(h.carla)
    hidden = client.get(_url(doc))
    unknown = client.get(f"/api/v1/workspaces/{h.workspace.id}/documents/{h.workspace.id}")
    assert hidden.status_code == 404
    assert hidden.json() == unknown.json()


def test_1c_shaped_client_reads_a_private_letter_without_new_fields(client: Client) -> None:
    h = home()
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    client.force_login(h.bob)
    (row,) = client.get(f"/api/v1/workspaces/{h.workspace.id}/documents").json()
    for fixture in ("openapi_1b.json", "openapi_1c.json"):
        schema = json.loads((FIXTURES / fixture).read_text())["components"]["schemas"]
        required = set(schema["DocumentSummaryOut"].get("required", []))
        assert required <= set(row), fixture


def test_share_without_making_private(client: Client) -> None:
    o = office()
    doc = o.letters["finance_letter"]
    client.force_login(o.finance)
    r = _set(client, doc, "workspace", (o.plain.id,))
    assert r.status_code == 200, r.json()
    assert r.json()["visibility"] == "workspace"
    assert r.json()["shared_user_ids"] == [str(o.plain.id)]
    assert sees(o.plain, doc)
    with as_actor(o.workspace, o.finance.id):
        assert not Document.objects.filter(id=doc.id, visibility="restricted").exists()


def test_the_history_shows_who_made_it_private() -> None:
    h = home()
    doc = letter(h.workspace, scanned_by=h.bob)
    with as_actor(h.workspace, h.anne.id):
        visibility.make_private(doc, actor=AuditActor.for_user(h.anne), origin="user")
    client = Client()
    client.force_login(h.bob)
    kinds = [e["kind"] for e in client.get(_url(doc, "/events")).json()]
    assert "made_private" in kinds

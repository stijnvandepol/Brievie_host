"""Editing an own document's details (archive plan Task 6, spec S 10): `require_editable`
first, a letter takes only "Bewaren tot", one history line per field and never the title."""

from datetime import date
from typing import TYPE_CHECKING

from django.test import Client

from apps.documents.models import Document, DocumentEvent
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.archive_support import own_document, private_own_document
from tests.documents_support import trashed
from tests.factories import UserFactory
from tests.my_tasks_support import household
from tests.visibility_support import as_actor, home, letter, office

if TYPE_CHECKING:
    from tests.helpers import Response

JSON = "application/json"


def _patch(client: Client, document: Document, body: dict[str, object]) -> "Response":
    return client.patch(
        f"/api/v1/workspaces/{document.workspace_id}/documents/{document.id}/details",
        body,
        content_type=JSON,
    )


def _stored(document: Document) -> Document:
    with as_actor(document.workspace, SYSTEM):
        return Document.objects.get(id=document.id)


def _lines(document: Document) -> list[tuple[str, str, date | None]]:
    with as_actor(document.workspace, SYSTEM):
        return sorted(
            (e.kind, e.note, e.on_date)
            for e in DocumentEvent.objects.filter(document=document).exclude(
                kind__in=["addressee_added", "label_added", "document_added"]
            )
        )


def test_editor_changes_title_type_and_dates(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, on=[h.persons["Piet"]], scanned_by=h.anne)
    client.force_login(h.piet)
    response = _patch(
        client,
        document,
        {
            "title": "Paspoort  nieuw",
            "document_type": "identity",
            "document_date": "2026-02-01",
            "expires_on": "2036-02-01",
            "remind_days": 90,
            "retain_until": "2040-01-01",
        },
    )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert (body["title"], body["letter_date"], body["expires_on"]) == (
        "Paspoort nieuw",
        "2026-02-01",
        "2036-02-01",
    )
    assert (body["remind_days"], body["retain_until"]) == (90, "2040-01-01")
    assert _lines(document) == sorted(
        [
            ("details_changed", "title", None),
            ("details_changed", "document_date", date(2026, 2, 1)),
            ("details_changed", "expires_on", date(2036, 2, 1)),
            ("details_changed", "remind_days", None),
            ("retention_changed", "", date(2040, 1, 1)),
        ]
    )


def test_viewer_and_emergency_reader_cannot_write(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, on=[h.persons["Piet"]], scanned_by=h.anne)
    viewer = UserFactory.create(name="Lezer")
    Membership.objects.create(user=viewer, workspace=h.workspace, role=Membership.Role.VIEWER)
    client.force_login(viewer)
    assert _patch(client, document, {"title": "Anders"}).status_code == 403

    w = home()
    Workspace.objects.filter(id=w.workspace.id).update(emergency_access=True)
    private = private_own_document(w.workspace, on=[w.persons["Bob"]])
    admin = Client()
    admin.force_login(w.anne)
    opened = admin.post(
        f"/api/v1/workspaces/{w.workspace.id}/persons/{w.persons['Bob'].id}/emergency-access",
        {"reason": "Bob ligt in het ziekenhuis, de polis is nodig"},
        content_type=JSON,
    )
    assert opened.status_code == 200, opened.json()
    response = _patch(admin, private, {"title": "Anders"})
    assert response.status_code == 403
    assert response.json()["code"] == "emergency_read_only"
    assert _stored(private).title == "Medisch dossier"


def test_invisible_or_trashed_document_is_404_not_403(client: Client) -> None:
    o = office()
    hidden = own_document(o.workspace, on=[o.persons["Finance"]], scanned_by=o.finance)
    client.force_login(o.plain)
    response = _patch(client, hidden, {"title": "Anders"})
    assert response.status_code == 404
    assert response.json()["code"] == "document_not_found"
    mine = trashed(own_document(o.workspace, on=[o.persons["Piet"]], scanned_by=o.plain))
    response = _patch(client, mine, {"title": "Anders"})
    assert response.status_code == 404


def test_letter_accepts_only_retain_until(client: Client) -> None:
    h = household()
    post = letter(h.workspace, on=[h.persons["Anne"]])
    client.force_login(h.anne)
    bodies: list[dict[str, object]] = [
        {"title": "Brief"},
        {"expires_on": "2030-01-01"},
        {"document_type": "invoice"},
        {"remind_days": 7},
    ]
    for body in bodies:
        response = _patch(client, post, body)
        assert response.status_code == 422, body
    response = _patch(client, post, {"retain_until": "2033-01-01"})
    assert response.status_code == 200
    assert response.json()["retain_until"] == "2033-01-01"
    assert _lines(post) == [("retention_changed", "", date(2033, 1, 1))]


def test_clear_flags_clear_and_empty_means_unchanged(client: Client) -> None:
    h = household()
    document = own_document(
        h.workspace,
        scanned_by=h.anne,
        expires_on=date(2031, 3, 12),
        retain_until=date(2035, 1, 1),
    )
    client.force_login(h.anne)
    assert _patch(client, document, {}).status_code == 200
    assert _lines(document) == []
    response = _patch(client, document, {"clear_expires_on": True, "clear_retain_until": True})
    assert response.status_code == 200
    stored = _stored(document)
    assert (stored.expires_on, stored.retain_until, stored.title) == (None, None, "Paspoort")
    both = _patch(client, document, {"expires_on": "2031-01-01", "clear_expires_on": True})
    assert both.status_code == 422


def test_type_must_belong_to_the_kind(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne)
    client.force_login(h.anne)
    assert _patch(client, document, {"document_type": "invoice"}).status_code == 422
    assert _patch(client, document, {"document_type": "diploma"}).status_code == 200


def test_history_names_fields_never_the_title(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne)
    client.force_login(h.anne)
    _patch(client, document, {"title": "Geheim plan van de huurder"})
    with as_actor(h.workspace, SYSTEM):
        rows = list(DocumentEvent.objects.filter(document=document).values_list("note", flat=True))
    assert rows == ["title"]
    assert not any("Geheim" in row for row in rows)


def test_kind_cannot_be_changed(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne)
    client.force_login(h.anne)
    response = _patch(client, document, {"kind": "letter", "status": "processing"})
    assert response.status_code == 200
    stored = _stored(document)
    assert (stored.kind, stored.status) == ("document", "done")

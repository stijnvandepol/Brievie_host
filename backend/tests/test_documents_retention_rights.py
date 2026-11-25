""" "Bewaren tot" under the security review of PR #54, point 3: every change is in the audit
log (the date and the document's id only), a date lies at most 20 years ahead, and an admin
and the person a document belongs to can always take the date away, also from a document in
the trash, so the purge (and an erasure request) is never blocked by another member's date."""

from datetime import date, timedelta

from django.test import Client
from django.utils import timezone

from apps.audit.models import WorkspaceAuditEvent
from apps.documents.models import Document
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.archive_support import own_document
from tests.documents_support import trashed
from tests.my_tasks_support import household
from tests.visibility_support import as_actor

JSON = "application/json"


def _details(client: Client, document: Document, body: dict[str, object]) -> int:
    response = client.patch(
        f"/api/v1/workspaces/{document.workspace_id}/documents/{document.id}/details",
        body,
        content_type=JSON,
    )
    return int(response.status_code)


def _rows(document: Document) -> list[WorkspaceAuditEvent]:
    with as_actor(document.workspace, SYSTEM):
        return list(
            WorkspaceAuditEvent.objects.filter(code="retention_changed").order_by("created_at")
        )


def test_setting_and_clearing_the_date_is_in_the_audit_log(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, title="Huurcontract van de huurder")
    client.force_login(h.piet)
    assert _details(client, document, {"retain_until": "2033-01-01"}) == 200
    assert _details(client, document, {"clear_retain_until": True}) == 200
    rows = _rows(document)
    assert [row.values for row in rows] == [{"until": "2033-01-01"}, {"until": ""}]
    assert {(row.target_kind, row.target_id) for row in rows} == {("document", document.id)}
    assert {row.actor_id for row in rows} == {h.piet.id}
    assert all("Huurcontract" not in repr((row.values, row.target_name)) for row in rows)


def test_a_date_lies_at_most_twenty_years_ahead(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne)
    client.force_login(h.anne)
    too_far = timezone.localdate() + timedelta(days=366 * 20 + 10)
    assert _details(client, document, {"retain_until": too_far.isoformat()}) == 422
    fine = timezone.localdate() + timedelta(days=365 * 19)
    assert _details(client, document, {"retain_until": fine.isoformat()}) == 200


def test_an_admin_takes_the_date_away_in_the_trash_and_can_then_purge(client: Client) -> None:
    h = household()
    kept = trashed(
        own_document(
            h.workspace,
            scanned_by=h.piet,
            retain_until=timezone.localdate() + timedelta(days=400),
        )
    )
    base = f"/api/v1/workspaces/{h.workspace.id}"
    client.force_login(h.anne)
    assert client.delete(f"{base}/trash/{kept.id}").status_code == 409
    # Only the date, only by an admin: anything else of a document in the trash stays 404.
    assert _details(client, kept, {"title": "Anders"}) == 404
    assert _details(client, kept, {"clear_retain_until": True}) == 200
    with as_actor(h.workspace, SYSTEM):
        assert Document.objects.get(id=kept.id).retain_until is None
    assert client.delete(f"{base}/trash/{kept.id}").status_code == 204
    assert [row.values for row in _rows(kept)] == [{"until": ""}]


def test_a_member_cannot_change_a_document_in_the_trash(client: Client) -> None:
    h = household()
    kept = trashed(own_document(h.workspace, scanned_by=h.piet, retain_until=date(2030, 1, 1)))
    client.force_login(h.piet)
    assert _details(client, kept, {"clear_retain_until": True}) == 404
    with as_actor(h.workspace, SYSTEM):
        assert Document.objects.get(id=kept.id).retain_until == date(2030, 1, 1)


def test_the_person_takes_the_date_away_in_the_trash(client: Client) -> None:
    h = household()
    kept = trashed(
        own_document(
            h.workspace,
            on=[h.persons["Piet"]],
            scanned_by=h.anne,
            retain_until=timezone.localdate() + timedelta(days=400),
        )
    )
    client.force_login(h.piet)
    # Only the date: anything else of a document in the trash stays 404.
    assert _details(client, kept, {"title": "Anders"}) == 404
    assert _details(client, kept, {"clear_retain_until": True}) == 200
    with as_actor(h.workspace, SYSTEM):
        assert Document.objects.get(id=kept.id).retain_until is None
    assert [(row.values, row.actor_id) for row in _rows(kept)] == [({"until": ""}, h.piet.id)]


def test_the_person_takes_the_date_away_with_a_read_only_role(client: Client) -> None:
    h = household()
    Membership.objects.filter(workspace=h.workspace, user=h.piet).update(
        role=Membership.Role.VIEWER
    )
    document = own_document(
        h.workspace, on=[h.persons["Piet"]], scanned_by=h.anne, retain_until=date(2031, 1, 1)
    )
    client.force_login(h.piet)
    # Setting or moving it stays an editor's.
    assert _details(client, document, {"retain_until": "2032-01-01"}) == 403
    assert _details(client, document, {"clear_retain_until": True}) == 200
    with as_actor(h.workspace, SYSTEM):
        assert Document.objects.get(id=document.id).retain_until is None


def test_the_trash_says_who_may_take_the_date_away(client: Client) -> None:
    h = household()
    kept = trashed(
        own_document(
            h.workspace,
            on=[h.persons["Piet"]],
            scanned_by=h.anne,
            retain_until=date(2031, 1, 1),
        )
    )
    other = trashed(
        own_document(
            h.workspace, on=[h.persons["Jan"]], scanned_by=h.piet, retain_until=date(2031, 1, 1)
        )
    )
    url = f"/api/v1/workspaces/{h.workspace.id}/trash"

    def flags() -> dict[str, bool]:
        items = client.get(url).json()["items"]
        return {item["id"]: item["can_clear_retention"] for item in items}

    client.force_login(h.piet)
    assert flags() == {str(kept.id): True, str(other.id): False}
    client.force_login(h.anne)
    assert flags() == {str(kept.id): True, str(other.id): True}

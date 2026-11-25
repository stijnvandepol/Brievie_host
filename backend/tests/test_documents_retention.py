""" "Bewaren tot" in `check_removal` (archive plan Task 9, spec S 7.2, Besluit B4): the trash
is allowed with a warning, purge-now and emptying refuse, the expiry leaves it, until the
day has passed or the date is removed."""

from datetime import date, timedelta

from django.test import Client
from django.utils import timezone

from apps.documents import purge, retention
from apps.documents.models import Document
from apps.documents.retention import RemovalVerdict
from apps.workspaces.context import SYSTEM
from tests.archive_support import own_document
from tests.documents_support import trashed
from tests.helpers import post_json
from tests.my_tasks_support import household
from tests.visibility_support import as_actor, letter

JSON = "application/json"
TODAY = date(2026, 11, 24)


def _stored(document: Document) -> Document:
    with as_actor(document.workspace, SYSTEM):
        return Document.objects.get(id=document.id)


def _retained(days: int = 30) -> date:
    return timezone.localdate() + timedelta(days=days)


def test_the_verdict_per_step_and_date() -> None:
    h = household()
    kept = own_document(h.workspace, scanned_by=h.anne, retain_until=date(2031, 1, 1))
    trash = retention.check_removal(kept, step="trash", today=TODAY)
    assert trash.allowed
    assert trash.warning == "Bewaren tot 1 januari 2031. Weet je het zeker?"
    refused = retention.check_removal(kept, step="purge", today=TODAY)
    assert not refused.allowed
    assert "1 januari 2031" in refused.warning
    # On the day itself it is still kept; the day after it may go.
    assert not retention.check_removal(kept, step="purge", today=date(2031, 1, 1)).allowed
    assert retention.check_removal(kept, step="purge", today=date(2031, 1, 2)) == RemovalVerdict()


def test_trash_is_allowed_with_a_warning_before_the_date(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, retain_until=_retained())
    client.force_login(h.anne)
    response = post_json(client, f"/workspaces/{h.workspace.id}/documents/{document.id}/trash")
    assert response.status_code == 200
    assert response.json()["warning"].startswith("Bewaren tot ")
    assert _stored(document).trashed_at is not None
    plain = own_document(h.workspace, scanned_by=h.anne)
    response = post_json(client, f"/workspaces/{h.workspace.id}/documents/{plain.id}/trash")
    assert response.json()["warning"] == ""


def test_purge_now_and_empty_refuse_before_the_date(client: Client) -> None:
    h = household()
    kept = trashed(own_document(h.workspace, scanned_by=h.anne, retain_until=_retained()))
    gone = trashed(own_document(h.workspace, scanned_by=h.anne))
    client.force_login(h.anne)
    response = client.delete(f"/api/v1/workspaces/{h.workspace.id}/trash/{kept.id}")
    assert response.status_code == 409
    assert response.json()["code"] == "document_retained"
    assert _stored(kept).purge_started_at is None
    response = post_json(client, f"/workspaces/{h.workspace.id}/trash/empty", {})
    assert response.status_code == 200
    assert response.json() == {"document_count": 1, "retained": 1}
    assert _stored(kept).purge_started_at is None
    assert _stored(gone).purge_started_at is not None


def test_expiry_job_skips_a_retained_document_and_purges_it_after_the_date() -> None:
    h = household()
    kept = trashed(
        own_document(h.workspace, scanned_by=h.anne, retain_until=_retained()), days_ago=40
    )
    assert purge.mark_expired(h.workspace.id, now=timezone.now()) == 0
    assert _stored(kept).purge_started_at is None
    # The day passed: the next run purges it.
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=kept.id).update(
            retain_until=timezone.localdate() - timedelta(days=1)
        )
    assert purge.mark_expired(h.workspace.id, now=timezone.now()) == 1
    assert _stored(kept).purge_started_at is not None


def test_clearing_the_date_allows_the_purge(client: Client) -> None:
    h = household()
    document = own_document(h.workspace, scanned_by=h.anne, retain_until=_retained())
    client.force_login(h.anne)
    base = f"/api/v1/workspaces/{h.workspace.id}"
    post_json(client, f"/workspaces/{h.workspace.id}/documents/{document.id}/trash")
    assert client.delete(f"{base}/trash/{document.id}").status_code == 409
    # The details of a document in the trash are not editable: restore, clear, trash again.
    post_json(client, f"/workspaces/{h.workspace.id}/documents/{document.id}/restore")
    response = client.patch(
        f"{base}/documents/{document.id}/details", {"clear_retain_until": True}, content_type=JSON
    )
    assert response.status_code == 200
    post_json(client, f"/workspaces/{h.workspace.id}/documents/{document.id}/trash")
    assert client.delete(f"{base}/trash/{document.id}").status_code == 204


def test_no_date_changes_nothing_for_letters_and_documents() -> None:
    h = household()
    for document in (letter(h.workspace, on=[]), own_document(h.workspace, scanned_by=h.anne)):
        assert retention.check_removal(document, step="trash") == RemovalVerdict()
        assert retention.check_removal(document, step="purge") == RemovalVerdict()


def test_a_letter_can_be_retained_too(client: Client) -> None:
    h = household()
    post = letter(h.workspace, on=[h.persons["Anne"]])
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=post.id).update(retain_until=_retained())
    trashed(post)
    client.force_login(h.anne)
    response = client.delete(f"/api/v1/workspaces/{h.workspace.id}/trash/{post.id}")
    assert response.status_code == 409


def test_retention_message_names_no_content() -> None:
    h = household()
    document = own_document(
        h.workspace,
        scanned_by=h.anne,
        title="Huurcontract",
        retain_until=date(2031, 1, 1),
        ocr_text="Geheime huurprijs",
    )
    warnings = [
        retention.check_removal(document, step="trash", today=TODAY).warning,
        retention.check_removal(document, step="purge", today=TODAY).warning,
    ]
    for warning in warnings:
        assert "Huurcontract" not in warning
        assert "Geheime" not in warning

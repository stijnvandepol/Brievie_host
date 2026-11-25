"""The archive list (archive plan Task 7, spec S 5): every reviewed document of both kinds
the member sees, with filters that only narrow."""

import uuid
from datetime import date, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.common.errors import ApiError
from apps.documents import services
from apps.documents.models import Document
from apps.documents.services import ArchiveFilters
from apps.workspaces.context import SYSTEM
from tests.archive_support import membership_of, own_document
from tests.my_tasks_support import Household, household
from tests.visibility_support import as_actor, letter


def _list(h: Household, filters: ArchiveFilters) -> set[uuid.UUID]:
    member = membership_of(h.workspace, h.anne)
    with as_actor(h.workspace, h.anne.id):
        return {d.id for d in services.list_documents(member, archive=filters)}


def _set(document: Document, **fields: object) -> None:
    with as_actor(document.workspace, SYSTEM):
        Document.objects.filter(id=document.id).update(**fields)


def test_archived_lists_done_documents_of_both_kinds_and_no_unreviewed_letter() -> None:
    h = household()
    done = letter(h.workspace, on=[h.persons["Anne"]])
    in_review = letter(h.workspace, on=[h.persons["Anne"]], status="needs_review")
    processing = letter(h.workspace, on=[], status="processing")
    own = own_document(h.workspace, scanned_by=h.anne)
    own_processing = own_document(h.workspace, scanned_by=h.anne, status="processing")
    archived = _list(h, ArchiveFilters(archived=True))
    assert archived == {done.id, own.id}
    assert {in_review.id, processing.id, own_processing.id}.isdisjoint(archived)


def test_filters_combine_and_each_narrows_never_widens() -> None:
    h = household()
    invoice = letter(h.workspace, on=[h.persons["Anne"]])
    _set(invoice, document_type="invoice", sender="Waterbedrijf")
    passport = own_document(h.workspace, scanned_by=h.anne, document_type="identity")
    diploma = own_document(h.workspace, scanned_by=h.anne, document_type="diploma")
    everything = _list(h, ArchiveFilters(archived=True))
    assert everything == {invoice.id, passport.id, diploma.id}
    assert _list(h, ArchiveFilters(archived=True, kind="document")) == {passport.id, diploma.id}
    assert _list(h, ArchiveFilters(archived=True, kind="letter")) == {invoice.id}
    assert _list(h, ArchiveFilters(archived=True, kind="document", document_type="diploma")) == {
        diploma.id
    }
    assert (
        _list(h, ArchiveFilters(archived=True, kind="document", document_type="invoice")) == set()
    )
    assert _list(h, ArchiveFilters(archived=True, sender="Waterbedrijf")) == {invoice.id}
    assert _list(h, ArchiveFilters(archived=True, sender="Waterbedrijf", kind="document")) == set()


def test_year_uses_letter_date_then_created_at() -> None:
    h = household()
    dated = own_document(h.workspace, scanned_by=h.anne, letter_date=date(2019, 6, 1))
    undated = own_document(h.workspace, scanned_by=h.anne)
    _set(undated, created_at=timezone.now().replace(year=2021, month=6, day=15))
    assert _list(h, ArchiveFilters(year=2019)) == {dated.id}
    assert _list(h, ArchiveFilters(year=2021)) == {undated.id}
    assert _list(h, ArchiveFilters(year=2020)) == set()


def test_expiring_lists_documents_ending_within_90_days_or_past() -> None:
    h = household()
    today = timezone.localdate()
    soon = own_document(h.workspace, scanned_by=h.anne, expires_on=today + timedelta(days=60))
    past = own_document(h.workspace, scanned_by=h.anne, expires_on=today - timedelta(days=3))
    later = own_document(h.workspace, scanned_by=h.anne, expires_on=today + timedelta(days=200))
    never = own_document(h.workspace, scanned_by=h.anne)
    found = _list(h, ArchiveFilters(expiring=True))
    assert found == {soon.id, past.id}
    assert {later.id, never.id}.isdisjoint(found)


@pytest.mark.parametrize(
    "filters",
    [
        ArchiveFilters(kind="folder"),
        ArchiveFilters(document_type="brief"),
        ArchiveFilters(year=1800),
    ],
)
def test_unknown_kind_or_type_is_422(filters: ArchiveFilters) -> None:
    h = household()
    with pytest.raises(ApiError) as caught:
        _list(h, filters)
    assert (caught.value.status, caught.value.code) == (422, "validation_error")


def test_the_api_takes_the_filters_and_answers_the_new_fields(client: Client) -> None:
    h = household()
    letter(h.workspace, on=[h.persons["Anne"]])
    own = own_document(
        h.workspace,
        scanned_by=h.anne,
        title="Garantiebewijs",
        document_type="warranty",
        expires_on=date(2031, 3, 12),
        retain_until=date(2033, 1, 1),
    )
    client.force_login(h.anne)
    url = f"/api/v1/workspaces/{h.workspace.id}/documents"
    response = client.get(url, {"archived": "true", "kind": "document"})
    assert response.status_code == 200
    [row] = response.json()
    assert row["id"] == str(own.id)
    assert (row["kind"], row["title"], row["document_type"]) == (
        "document",
        "Garantiebewijs",
        "warranty",
    )
    assert (row["expires_on"], row["retain_until"], row["letter_status"]) == (
        "2031-03-12",
        "2033-01-01",
        "",
    )
    assert client.get(url, {"kind": "folder"}).status_code == 422


def test_a_sender_is_never_a_query_parameter_but_a_body_field(client: Client) -> None:
    """Security review of PR #54, point 1: a sender's name ("GGZ …", "Deurwaarder …") would
    end up in proxy logs and the browser history. It is chosen in the body of
    `POST listArchive`; `GET listDocuments` has no `sender`."""
    h = household()
    water = letter(h.workspace, on=[h.persons["Anne"]])
    _set(water, sender="Waterbedrijf")
    other = letter(h.workspace, on=[h.persons["Anne"]])
    _set(other, sender="Gemeente")
    client.force_login(h.anne)
    url = f"/api/v1/workspaces/{h.workspace.id}/documents"
    listed = client.get(url, {"archived": "true", "sender": "Waterbedrijf"})
    assert {row["id"] for row in listed.json()} == {str(water.id), str(other.id)}
    response = client.post(
        f"{url}/archive",
        {"sender": "Waterbedrijf", "kind": "letter"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.json()
    assert [row["id"] for row in response.json()] == [str(water.id)]
    assert client.post(f"{url}/archive", {}, content_type="application/json").status_code == 200
    bad = client.post(f"{url}/archive", {"kind": "folder"}, content_type="application/json")
    assert bad.status_code == 422

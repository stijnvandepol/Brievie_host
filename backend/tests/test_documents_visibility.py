"""The visibility seam: every member-facing read of documents starts from one function."""

import pytest
from django.db.models import QuerySet
from django.test import Client

from apps.documents import services, visibility
from apps.documents.models import Document
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, documents_url, make_document


def membership_of(workspace: Workspace) -> Membership:
    return Membership.objects.get(workspace=workspace, role="admin")


def test_a_member_sees_the_documents_of_the_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    mine = make_document(ws_a, "done")
    make_document(ws_b, "done")

    with workspace_context(ws_a.id):
        found = list(visibility.visible_documents(membership_of(ws_a)))

    assert [d.id for d in found] == [mine.id]


def test_another_workspace_is_not_visible_even_inside_its_context(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    make_document(ws_b, "done")

    # The seam filters on the membership's workspace as well as RLS doing so.
    with workspace_context(ws_b.id):
        assert not visibility.visible_documents(membership_of(ws_a)).exists()


def test_without_workspace_context_nothing_is_visible(ws_a: Workspace) -> None:
    make_document(ws_a, "done")

    assert not visibility.visible_documents(membership_of(ws_a)).exists()


@pytest.fixture
def nothing_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam hides everything: every read built on it must then find nothing."""

    def none(membership: Membership) -> QuerySet[Document]:
        return Document.objects.none()

    monkeypatch.setattr(visibility, "visible_documents", none)


@pytest.mark.usefixtures("nothing_visible")
def test_list_counts_and_detail_start_from_the_seam(client: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "processing")
    client.force_login(admin_of(ws_a))

    listed = client.get(documents_url(ws_a.id))
    counts = client.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts")
    detail = client.get(f"{documents_url(ws_a.id)}/{document.id}")
    events = client.get(f"{documents_url(ws_a.id)}/{document.id}/events")
    page = client.get(f"{documents_url(ws_a.id)}/{document.id}/pages/1")

    assert listed.json() == []
    assert counts.json()["new"] == 0
    assert detail.status_code == 404
    assert events.status_code == 404
    assert page.status_code == 404


@pytest.mark.usefixtures("nothing_visible")
def test_services_take_the_membership(ws_a: Workspace) -> None:
    make_document(ws_a, "processing")
    membership = membership_of(ws_a)

    with workspace_context(ws_a.id):
        assert services.list_documents(membership) == []
        assert services.inbox_counts(membership)["new"] == 0

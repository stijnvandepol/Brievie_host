"""Confirm with visibility, inside the write order of S 4.9.3 (plan Task 30, PB T18)."""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from django.test import Client

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.documents import visibility
from apps.documents.models import Document, DocumentEvent
from apps.labels.models import Label
from apps.people.models import Person
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.visibility_support import Home, as_actor, home, letter, readers

if TYPE_CHECKING:
    from tests.helpers import Response

TASK = {"type": "pay", "description": "Betalen", "deadline_source": "manual"}


def _zorg(workspace: Workspace) -> Label:
    with as_actor(workspace, SYSTEM):
        return Label.objects.get(workspace=workspace, system_key="zorg")


def _confirm(client: Client, doc: Document, **extra: object) -> "Response":
    body: dict[str, object] = {
        "sender": "Huisarts",
        "document_type": "",
        "summary": "",
        "summary_by_ai": False,
        "tasks": [TASK],
        **extra,
    }
    return client.post(
        f"/api/v1/workspaces/{doc.workspace_id}/documents/{doc.id}/confirm",
        body,
        content_type="application/json",
    )


def _stored(doc: Document) -> Document:
    with as_actor(doc.workspace, SYSTEM):
        return Document.objects.get(id=doc.id)


def _tasks(doc: Document) -> int:
    with as_actor(doc.workspace, SYSTEM):
        return Task.objects.filter(document=doc).count()


def _in_review(
    workspace: Workspace, *, on: Sequence[Person] = (), scanned_by: User | None = None
) -> Document:
    """A letter waiting for review, without the helper's task (the review brings its own)."""
    doc = letter(workspace, on=on, scanned_by=scanned_by, status="needs_review")
    with as_actor(workspace, SYSTEM):
        Task.objects.filter(document=doc).delete()
    return doc


def _private_in_review(h: Home, *, on: str, shared: tuple[str, ...] = ()) -> Document:
    doc = _in_review(h.workspace, on=[h.persons[on]])
    with as_actor(h.workspace, SYSTEM):
        users = {"Anne": h.anne, "Bob": h.bob, "Carla": h.carla}
        visibility.make_private(
            doc,
            actor=AuditActor.system(),
            origin="user",
            share_user_ids=[users[name].id for name in shared],
        )
    return doc


def test_confirm_with_zorg_label_stores_everything_and_is_private(client: Client) -> None:
    h = home()
    doc = _in_review(h.workspace, scanned_by=h.carla)
    client.force_login(h.bob)
    r = _confirm(
        client,
        doc,
        sets_organisation=True,
        label_ids=[str(_zorg(h.workspace).id)],
        addressee_ids=[],
    )
    assert r.status_code == 200, r.json()
    stored = _stored(doc)
    assert (stored.status, stored.visibility, stored.sender) == ("done", "restricted", "Huisarts")
    assert readers(doc) == {("Bob", "share"), ("Carla", "share")}
    with as_actor(h.workspace, h.bob.id):
        assert DocumentEvent.objects.filter(document=doc, kind="confirmed").exists()
        assert Task.objects.filter(document=doc, description="Betalen").exists()


def test_confirm_with_visibility_restricted(client: Client) -> None:
    h = home()
    doc = _in_review(h.workspace, on=[h.persons["Oma"]])
    client.force_login(h.bob)
    r = _confirm(client, doc, visibility="restricted")
    assert r.status_code == 200, r.json()
    assert r.json()["visibility"] == "restricted"
    assert readers(doc) == {("Bob", "share")}


def test_confirm_workspace_on_zorg_letter_by_addressee_is_public(client: Client) -> None:
    h = home()
    doc = _private_in_review(h, on="Bob")
    client.force_login(h.bob)
    r = _confirm(client, doc, visibility="workspace")
    assert r.status_code == 200, r.json()
    assert _stored(doc).visibility == "workspace"


def test_confirm_workspace_by_non_addressee_is_403_and_stores_nothing(client: Client) -> None:
    h = home()
    doc = _private_in_review(h, on="Bob", shared=("Carla",))
    client.force_login(h.carla)
    r = _confirm(client, doc, visibility="workspace")
    assert (r.status_code, r.json()["code"]) == (403, "cannot_make_public")
    stored = _stored(doc)
    assert (stored.status, stored.visibility, stored.sender) == ("needs_review", "restricted", "")
    assert _tasks(doc) == 0


def test_confirm_removing_own_addressee_on_a_private_letter_is_409(client: Client) -> None:
    h = home()
    doc = _private_in_review(h, on="Bob")
    client.force_login(h.bob)
    r = _confirm(client, doc, sets_organisation=True, label_ids=[], addressee_ids=[])
    assert (r.status_code, r.json()["code"]) == (409, "would_lose_access")
    assert _stored(doc).status == "needs_review"
    assert _tasks(doc) == 0


def test_confirm_without_visibility_field_behaves_as_1c(client: Client) -> None:
    h = home()
    doc = _in_review(h.workspace, scanned_by=h.carla)
    client.force_login(h.bob)
    assert _confirm(client, doc).status_code == 200
    assert _stored(doc).visibility == "workspace"
    assert readers(doc) == set()


def test_an_unknown_visibility_is_422(client: Client) -> None:
    h = home()
    doc = _in_review(h.workspace)
    client.force_login(h.bob)
    assert _confirm(client, doc, visibility="geheim").status_code == 422


def test_repeated_confirm_of_a_private_letter_is_200(client: Client) -> None:
    h = home()
    doc = _in_review(h.workspace, on=[h.persons["Oma"]])
    client.force_login(h.bob)
    first = _confirm(client, doc, visibility="restricted")
    assert first.status_code == 200
    again = _confirm(client, doc, visibility="restricted")
    assert again.status_code == 200, again.json()
    assert again.json()["id"] == first.json()["id"]
    assert _tasks(doc) == 1

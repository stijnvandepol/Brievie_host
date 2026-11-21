"""A trashed letter is out of every read: through the seam (`visible_documents`) and on
the paths off it (tasks, member export, person and label counts). The trash itself is
read only through `trashed_documents`, on the same rights as the seam."""

import uuid

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.documents import export, member_export, visibility
from apps.documents.models import Document, DocumentAddressee, DocumentLabel
from apps.labels import services as label_services
from apps.labels.models import Label
from apps.people.models import Person
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, documents_url, make_document, make_person, trashed
from tests.visibility_support import as_actor, office


def membership_of(workspace: Workspace, user: User) -> Membership:
    return Membership.objects.get(workspace=workspace, user=user)


def admin_membership(workspace: Workspace) -> Membership:
    return Membership.objects.get(workspace=workspace, role="admin")


def set_fields(document: Document, **fields: object) -> None:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(**fields)


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def test_list_counts_and_search_leave_out_trashed(client_a: Client, ws_a: Workspace) -> None:
    live = make_document(ws_a, "processing")
    gone = make_document(ws_a, "processing")
    for document in (live, gone):
        set_fields(document, sender="Belastingdienst", ocr_text="aanslag inkomstenbelasting")
    before = client_a.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts").json()
    trashed(gone)

    listed = client_a.get(documents_url(ws_a.id))
    counts = client_a.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts").json()
    found = client_a.post(
        f"{documents_url(ws_a.id)}/search",
        {"q": "belastingdienst"},
        content_type="application/json",
    )

    assert [row["id"] for row in listed.json()] == [str(live.id)]
    assert before["new"] == 2
    assert counts["new"] == 1
    assert [hit["id"] for hit in found.json()["items"]] == [str(live.id)]


def test_get_document_of_trashed_is_404_in_trash(client_a: Client, ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a, "done"))
    base = f"{documents_url(ws_a.id)}/{document.id}"

    for path in (base, f"{base}/pages/1", f"{base}/events"):
        r = client_a.get(path)
        assert r.status_code == 404, path
        assert r.json()["code"] == "document_in_trash", path
        assert r.json()["message"] == "Deze brief staat in de prullenbak."


def test_get_document_purge_started_is_plain_not_found(client_a: Client, ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a, "done"), purge_started=True)

    r = client_a.get(f"{documents_url(ws_a.id)}/{document.id}")

    assert r.status_code == 404
    assert r.json()["code"] == "document_not_found"


def test_trashed_documents_lists_only_trash_not_purging(ws_a: Workspace) -> None:
    make_document(ws_a, "done")
    in_trash = trashed(make_document(ws_a, "done"))
    trashed(make_document(ws_a, "done"), purge_started=True)
    membership = admin_membership(ws_a)

    with workspace_context(ws_a.id, actor=membership.user_id):
        ids = list(visibility.trashed_documents(membership).values_list("id", flat=True))

    assert ids == [in_trash.id]


def test_trash_never_crosses_workspaces(ws_a: Workspace, ws_b: Workspace) -> None:
    trashed(make_document(ws_b, "done"))
    membership = admin_membership(ws_a)

    with workspace_context(ws_a.id, actor=membership.user_id):
        assert not visibility.trashed_documents(membership).exists()
    # The seam filters on the membership's workspace as well as RLS doing so.
    with workspace_context(ws_b.id, actor=membership.user_id):
        assert not visibility.trashed_documents(membership).exists()


def test_trashed_documents_follow_the_policies() -> None:
    o = office()
    for letter in o.letters.values():
        trashed(letter)
    names = {letter.id: name for name, letter in o.letters.items()}

    for user in o.users.values():
        membership = membership_of(o.workspace, user)
        with as_actor(o.workspace, user.id):
            ids = list(visibility.trashed_documents(membership).values_list("id", flat=True))
            count = visibility.trashed_documents(membership).count()
            assert not visibility.visible_documents(membership).exists()
        expected = o.visible(user.id)
        assert {names[i] for i in ids} == expected, user.name
        assert count == len(expected), user.name
    assert "private_femke" in o.visible(o.finance.id)
    assert "private_femke" not in o.visible(o.admin.id)


def test_trashed_documents_refuses_the_system(ws_a: Workspace) -> None:
    membership = admin_membership(ws_a)

    with pytest.raises(RuntimeError), workspace_context(ws_a.id, actor=SYSTEM):
        visibility.trashed_documents(membership)
    with pytest.raises(RuntimeError), workspace_context(ws_a.id, actor=uuid.uuid4()):
        visibility.trashed_documents(membership)


def test_tasks_of_trashed_letter_hidden(client_a: Client, ws_a: Workspace) -> None:
    live = make_document(ws_a, "done")
    gone = trashed(make_document(ws_a, "done"))
    with workspace_context(ws_a.id, actor=SYSTEM):
        on_live = Task.objects.create(workspace=ws_a, document=live, type="pay", description="a")
        loose = Task.objects.create(workspace=ws_a, type="pay", description="b")
        Task.objects.create(workspace=ws_a, document=gone, type="pay", description="c")

    r = client_a.get(f"/api/v1/workspaces/{ws_a.id}/tasks?status=all")

    assert r.status_code == 200
    assert {row["id"] for row in r.json()} == {str(on_live.id), str(loose.id)}


def test_member_export_leaves_out_trashed(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        mine = Person.objects.get(workspace=ws_a, user=admin)
    letters = [make_document(ws_a, "done") for _ in range(2)]
    with workspace_context(ws_a.id, actor=SYSTEM):
        for letter in letters:
            DocumentAddressee.objects.create(workspace=ws_a, document=letter, person=mine)
    live, gone = letters
    trashed(gone)

    assert member_export.member_document_ids(ws_a.id, admin.id) == [live.id]
    assert export._exists(ws_a.id, admin.id, gone.id) is False
    assert export._exists(ws_a.id, None, gone.id) is False
    assert export._read_document(ws_a.id, None, gone.id) is None
    assert export._exists(ws_a.id, None, live.id) is True


def test_person_and_label_counts_leave_out_trashed(client_a: Client, ws_a: Workspace) -> None:
    person = make_person(ws_a, "Oma")
    letters = [make_document(ws_a, "done") for _ in range(2)]
    with workspace_context(ws_a.id, actor=SYSTEM):
        label = Label.objects.create(workspace=ws_a, name="Zwaluwstaart")
        for letter in letters:
            DocumentAddressee.objects.create(workspace=ws_a, document=letter, person=person)
            DocumentLabel.objects.create(workspace=ws_a, document=letter, label=label)
            Document.objects.filter(id=letter.id).update(addressee_ids=[person.id])
    trashed(letters[1])

    persons = client_a.get(f"/api/v1/workspaces/{ws_a.id}/persons").json()
    labels = client_a.get(f"/api/v1/workspaces/{ws_a.id}/labels").json()

    assert next(p for p in persons if p["id"] == str(person.id))["document_count"] == 1
    assert next(lb for lb in labels if lb["id"] == str(label.id))["document_count"] == 1


def test_label_count_follows_the_policies_and_the_trash() -> None:
    """Every user counts the live letters they may see: neither a trashed letter (seen
    or not) nor a live one they may not see."""
    o = office()
    with workspace_context(o.workspace.id, actor=SYSTEM):
        label = Label.objects.create(workspace=o.workspace, name="Zwaluwstaart")
        for letter in o.letters.values():
            DocumentLabel.objects.create(workspace=o.workspace, document=letter, label=label)
    gone = {name for index, name in enumerate(sorted(o.letters)) if index % 2 == 0}
    for name in gone:
        trashed(o.letters[name])

    for user in o.users.values():
        with as_actor(o.workspace, user.id):
            counts = {lb.id: lb.document_count for lb in label_services.list_labels(o.workspace)}  # type: ignore[attr-defined]
        assert counts[label.id] == len(o.visible(user.id) - gone), user.name


def test_automatic_list_and_count_leave_out_trashed(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    set_fields(document, has_automatic_link=True)
    before = client_a.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts").json()["automatic"]
    trashed(document)

    listed = client_a.get(f"{documents_url(ws_a.id)}?automatic=true").json()
    after = client_a.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts").json()["automatic"]

    assert before == 1
    assert listed == []
    assert after == 0


def test_delete_person_on_trashed_letter_names_trash(client_a: Client, ws_a: Workspace) -> None:
    person = make_person(ws_a, "Oma")
    document = make_document(ws_a, "done")
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentAddressee.objects.create(workspace=ws_a, document=document, person=person)
    trashed(document)

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/persons/{person.id}")

    assert r.status_code == 409
    assert r.json()["code"] == "person_in_use"
    assert "prullenbak" in r.json()["message"]


@pytest.mark.parametrize("purge_started", [False, True])
def test_delete_person_on_trashed_letter_admin_cannot_see_keeps_the_plain_message(
    client: Client, purge_started: bool
) -> None:
    o = office()
    private = o.letters["private_femke"]
    assert "private_femke" not in o.visible(o.admin.id)
    with workspace_context(o.workspace.id, actor=SYSTEM):
        person = Person.objects.create(workspace=o.workspace, display_name="Kees")
        DocumentAddressee.objects.create(workspace=o.workspace, document=private, person=person)
    trashed(private, by=o.finance, purge_started=purge_started)
    client.force_login(o.admin)

    r = client.delete(f"/api/v1/workspaces/{o.workspace.id}/persons/{person.id}")

    assert r.status_code == 409
    assert r.json()["code"] == "person_in_use"
    assert "prullenbak" not in r.json()["message"]


def test_delete_person_on_trashed_letter_purge_started_keeps_the_plain_message(
    client_a: Client, ws_a: Workspace
) -> None:
    person = make_person(ws_a, "Oma")
    document = make_document(ws_a, "done")
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentAddressee.objects.create(workspace=ws_a, document=document, person=person)
    trashed(document, purge_started=True)

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/persons/{person.id}")

    assert r.status_code == 409
    assert "prullenbak" not in r.json()["message"]


def test_delete_person_on_live_letter_keeps_the_plain_message(
    client_a: Client, ws_a: Workspace
) -> None:
    person = make_person(ws_a, "Oma")
    letters = [make_document(ws_a, "done") for _ in range(2)]
    with workspace_context(ws_a.id, actor=SYSTEM):
        for letter in letters:
            DocumentAddressee.objects.create(workspace=ws_a, document=letter, person=person)
    trashed(letters[0])

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/persons/{person.id}")

    assert r.status_code == 409
    assert "prullenbak" not in r.json()["message"]


def test_purge_needs_trash_constraint(ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")

    with (
        workspace_context(ws_a.id, actor=SYSTEM),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        Document.objects.filter(id=document.id).update(purge_started_at=timezone.now())

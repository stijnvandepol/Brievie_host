"""Adding an own document through `createDocument` (archive plan Task 4, spec S 6.2, S 8):
it is checked before anything is stored, organised through `organise`, private only when
asked, and nothing in the request widens who reads it."""

import uuid

from django.test import Client
from django.utils import timezone

from apps.documents import reading, services
from apps.documents.models import Document, DocumentEvent
from apps.labels.models import Label
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.archive_support import membership_of
from tests.documents_support import jpeg, metadata_for, upload
from tests.factories import UserFactory
from tests.my_tasks_support import Household, household
from tests.visibility_support import as_actor, office, sees


def _document_metadata(page: bytes, **fields: object) -> dict[str, object]:
    return {
        **metadata_for([page]),
        "kind": "document",
        "title": "Diploma",
        # Not a sensitive type: these tests are not about Besluit B3 (see the one that is).
        "document_type": "diploma",
        **fields,
    }


def _label(h: Household) -> Label:
    # A label of its own: the preset Zorg would make the document private (S 4.6).
    with as_actor(h.workspace, SYSTEM):
        return Label.objects.create(workspace=h.workspace, name="Paperassen")


def _events(document_id: str, workspace: Workspace) -> list[tuple[str, str, object]]:
    with as_actor(workspace, SYSTEM):
        return sorted(
            (e.kind, e.origin, e.actor_id)
            for e in DocumentEvent.objects.filter(document_id=document_id)
        )


def test_old_client_without_new_fields_creates_a_letter(client: Client) -> None:
    h = household()
    client.force_login(h.anne)
    response = upload(client, h.workspace.id, [jpeg()])
    assert response.status_code == 201
    body = response.json()
    assert (body["kind"], body["title"], body["status"]) == ("letter", "", "processing")
    assert (body["expires_on"], body["retain_until"], body["remind_days"]) == (None, None, 30)


def test_a_letter_with_a_title_is_422(client: Client) -> None:
    h = household()
    client.force_login(h.anne)
    page = jpeg()
    response = upload(
        client, h.workspace.id, [page], metadata={**metadata_for([page]), "title": "Paspoort"}
    )
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    with as_actor(h.workspace, SYSTEM):
        assert not Document.objects.exists()


def test_document_needs_title_and_a_document_type(client: Client) -> None:
    h = household()
    client.force_login(h.anne)
    page = jpeg()
    for fields in ({"title": ""}, {"document_type": ""}, {"document_type": "invoice"}):
        metadata = _document_metadata(page, **fields)
        response = upload(client, h.workspace.id, [page], metadata=metadata)
        assert response.status_code == 422, fields
        assert response.json()["code"] == "validation_error"
        assert response.json()["message"].endswith(".")
    with as_actor(h.workspace, SYSTEM):
        assert not Document.objects.exists()


def test_document_gets_persons_labels_and_history_through_organise(client: Client) -> None:
    h = household()
    label = _label(h)
    client.force_login(h.piet)
    page = jpeg()
    response = upload(
        client,
        h.workspace.id,
        [page],
        metadata=_document_metadata(
            page,
            person_ids=[str(h.persons["Piet"].id)],
            label_ids=[str(label.id)],
            document_date="2026-01-10",
            expires_on="2031-03-12",
            remind_days=60,
            retain_until="2033-01-01",
        ),
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert (body["kind"], body["title"], body["document_type"]) == (
        "document",
        "Diploma",
        "diploma",
    )
    assert (body["letter_date"], body["expires_on"], body["remind_days"]) == (
        "2026-01-10",
        "2031-03-12",
        60,
    )
    assert body["retain_until"] == "2033-01-01"
    assert body["addressee_ids"] == [str(h.persons["Piet"].id)]
    assert body["label_ids"] == [str(label.id)]
    assert body["inbox_state"] == ""
    assert _events(body["id"], h.workspace) == sorted(
        [
            ("addressee_added", "user", h.piet.id),
            ("document_added", "user", h.piet.id),
            ("label_added", "user", h.piet.id),
        ]
    )
    with as_actor(h.workspace, SYSTEM):
        stored = Document.objects.get(id=body["id"])
    assert stored.addressee_ids == [h.persons["Piet"].id]
    assert stored.kind == "document"


def test_document_for_a_person_the_member_may_not_name_is_422(client: Client) -> None:
    h = household()
    client.force_login(h.anne)
    page = jpeg()
    archived = h.persons["Jan"]
    with as_actor(h.workspace, SYSTEM):
        Person.objects.filter(id=archived.id).update(archived_at=timezone.now())
    for person_id in (uuid.uuid4(), archived.id):
        response = upload(
            client,
            h.workspace.id,
            [page],
            metadata=_document_metadata(page, person_ids=[str(person_id)]),
        )
        assert response.status_code == 422
        assert response.json()["code"] == "validation_error"
    response = upload(
        client,
        h.workspace.id,
        [page],
        metadata=_document_metadata(page, label_ids=[str(uuid.uuid4())]),
    )
    assert response.status_code == 422
    with as_actor(h.workspace, SYSTEM):
        assert not Document.objects.exists()


def test_private_switch_makes_it_restricted_and_the_uploader_still_reads_it(
    client: Client,
) -> None:
    o = office()
    client.force_login(o.plain)
    page = jpeg()
    response = upload(
        client,
        o.workspace.id,
        [page],
        metadata=_document_metadata(page, person_ids=[str(o.persons["Piet"].id)], private=True),
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["visibility"] == "restricted"
    with as_actor(o.workspace, SYSTEM):
        document = Document.objects.get(id=body["id"])
    assert sees(o.plain, document)
    for other in (o.admin, o.sorter, o.finance, o.hr, o.viewer):
        assert not sees(other, document), other.name


def test_private_false_never_widens_a_workspace_that_hides_post(client: Client) -> None:
    o = office()
    client.force_login(o.plain)
    page = jpeg()
    response = upload(
        client,
        o.workspace.id,
        [page],
        metadata=_document_metadata(page, person_ids=[str(o.persons["Piet"].id)], private=False),
    )
    assert response.status_code == 201
    with as_actor(o.workspace, SYSTEM):
        document = Document.objects.get(id=response.json()["id"])
    assert document.visibility == "workspace"
    # Seen as a letter on Piet is seen: by Piet; not by a member of another department.
    assert sees(o.plain, document)
    assert not sees(o.hr, document)
    assert not sees(o.finance, document)


def test_kind_and_status_cannot_be_chosen_by_the_request_to_widen(client: Client) -> None:
    h = household()
    client.force_login(h.anne)
    page = jpeg()
    response = upload(
        client,
        h.workspace.id,
        [page],
        metadata=_document_metadata(page, status="done", visibility="workspace", confirmed=True),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "processing"
    with as_actor(h.workspace, SYSTEM):
        stored = Document.objects.get(id=body["id"])
    assert (stored.status, stored.confirmed_by_id, stored.confirmed_at) == (
        "processing",
        None,
        None,
    )


def test_repeating_the_upload_changes_nothing(client: Client) -> None:
    h = household()
    client.force_login(h.anne)
    page = jpeg()
    metadata = _document_metadata(page, person_ids=[str(h.persons["Anne"].id)])
    first = upload(client, h.workspace.id, [page], metadata=metadata)
    events = _events(first.json()["id"], h.workspace)
    again = upload(client, h.workspace.id, [page], metadata=metadata)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"]
    assert _events(first.json()["id"], h.workspace) == events


def test_viewer_cannot_add(client: Client) -> None:
    h = household()
    viewer = UserFactory.create(name="Lezer")
    Membership.objects.create(user=viewer, workspace=h.workspace, role=Membership.Role.VIEWER)
    client.force_login(viewer)
    page = jpeg()
    response = upload(client, h.workspace.id, [page], metadata=_document_metadata(page))
    assert response.status_code == 403
    with as_actor(h.workspace, SYSTEM):
        assert not Document.objects.exists()


def test_uploader_does_not_see_their_own_document_as_unread(client: Client) -> None:
    h = household()
    client.force_login(h.piet)
    page = jpeg()
    response = upload(
        client,
        h.workspace.id,
        [page],
        metadata=_document_metadata(page, person_ids=[str(h.persons["Piet"].id)]),
    )
    assert response.status_code == 201
    assert response.json()["unread"] is False
    member = membership_of(h.workspace, h.anne)
    with as_actor(h.workspace, h.anne.id):
        rows = services.list_documents(member)
        assert reading.unread_ids(member, rows) == set()


def test_a_sensitive_type_without_private_is_made_private(client: Client) -> None:
    """Security review of PR #54, point 8: an app that sends no `private` gets an identity
    document private (Besluit B3); an explicit false keeps it as the workspace says."""
    o = office()
    client.force_login(o.plain)
    page = jpeg()
    on_piet = [str(o.persons["Piet"].id)]
    response = upload(
        client,
        o.workspace.id,
        [page],
        metadata=_document_metadata(page, document_type="identity", person_ids=on_piet),
    )
    assert response.status_code == 201
    assert response.json()["visibility"] == "restricted"
    other = jpeg("ander")
    response = upload(
        client,
        o.workspace.id,
        [other],
        metadata=_document_metadata(
            other, document_type="identity", person_ids=on_piet, private=False
        ),
    )
    assert response.json()["visibility"] == "workspace"

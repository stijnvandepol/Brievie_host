import uuid
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.people.models import Person
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.factories import UserFactory
from tests.helpers import SETUP_TOKEN, post_json

if TYPE_CHECKING:
    from pytest_django import Settings

    from tests.helpers import Response


def persons_url(workspace_id: uuid.UUID | str) -> str:
    return f"/api/v1/workspaces/{workspace_id}/persons"


def create(client: Client, workspace_id: uuid.UUID | str, body: dict[str, object]) -> "Response":
    return post_json(client, f"/workspaces/{workspace_id}/persons", body)


def put(client: Client, path: str, body: dict[str, object]) -> "Response":
    return client.put(f"/api/v1{path}", body, content_type="application/json")


def admin_of(workspace: Workspace) -> User:
    return Membership.objects.get(workspace=workspace, role="admin").user


def join(workspace: Workspace, role: str) -> User:
    user = UserFactory.create()
    Membership.objects.create(user=user, workspace=workspace, role=role)
    return user


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def test_create_and_list_persons(client_a: Client, ws_a: Workspace) -> None:
    r = create(client_a, ws_a.id, {"display_name": " Oma ", "name_variants": ["G. Jansen"]})
    assert r.status_code == 201
    oma = r.json()
    assert oma["display_name"] == "Oma"
    assert oma["name_variants"] == ["G. Jansen"]
    assert oma["user_id"] is None
    assert oma["archived"] is False
    assert oma["document_count"] == 0

    r = create(client_a, ws_a.id, {"display_name": "Archief", "archived": True})
    assert r.status_code == 201
    assert r.json()["archived"] is True

    r = client_a.get(persons_url(ws_a.id))
    assert r.status_code == 200
    names = [p["display_name"] for p in r.json()]
    # The admin's own person, then Oma by name; archived ones last.
    assert names[-1] == "Archief"
    assert sorted(names[:-1], key=str.lower) == names[:-1]


def test_display_name_unique_ignoring_case(client_a: Client, ws_a: Workspace) -> None:
    assert create(client_a, ws_a.id, {"display_name": "Papa"}).status_code == 201

    r = create(client_a, ws_a.id, {"display_name": "papa"})

    assert r.status_code == 409
    assert r.json()["code"] == "person_exists"


def test_variants_are_cleaned(client_a: Client, ws_a: Workspace) -> None:
    r = create(
        client_a,
        ws_a.id,
        {"display_name": "Jan", "name_variants": [" J. de Vries ", "j. de vries", "x"]},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"

    r = create(
        client_a,
        ws_a.id,
        {"display_name": "Jan", "name_variants": [" J. de Vries ", "j. de vries", "Jan de Vries"]},
    )
    assert r.status_code == 201
    assert r.json()["name_variants"] == ["J. de Vries", "Jan de Vries"]


def test_too_many_variants_are_refused(client_a: Client, ws_a: Workspace) -> None:
    variants = [f"Naam {i}" for i in range(21)]

    r = create(client_a, ws_a.id, {"display_name": "Jan", "name_variants": variants})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_blank_display_name_is_422(client_a: Client, ws_a: Workspace) -> None:
    r = create(client_a, ws_a.id, {"display_name": "   "})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_bsn_in_a_name_is_masked(client_a: Client, ws_a: Workspace) -> None:
    r = create(
        client_a, ws_a.id, {"display_name": "Jan 111222333", "name_variants": ["J 111222333"]}
    )

    assert r.status_code == 201
    assert r.json()["display_name"] == "Jan [BSN]"
    assert r.json()["name_variants"] == ["J [BSN]"]
    with workspace_context(ws_a.id):
        assert Person.objects.filter(display_name="Jan [BSN]").exists()


def test_update_person(client_a: Client, ws_a: Workspace) -> None:
    person = create(client_a, ws_a.id, {"display_name": "Oma"}).json()

    r = put(
        client_a,
        f"/workspaces/{ws_a.id}/persons/{person['id']}",
        {"display_name": "Oma Jansen", "name_variants": ["G. Jansen"], "archived": True},
    )

    assert r.status_code == 200
    assert r.json()["display_name"] == "Oma Jansen"
    assert r.json()["name_variants"] == ["G. Jansen"]
    assert r.json()["archived"] is True

    r = put(client_a, f"/workspaces/{ws_a.id}/persons/{person['id']}", {"display_name": "Oma"})
    assert r.json()["name_variants"] == []
    assert r.json()["archived"] is False


def test_update_to_an_existing_name_is_409(client_a: Client, ws_a: Workspace) -> None:
    create(client_a, ws_a.id, {"display_name": "Papa"})
    mama = create(client_a, ws_a.id, {"display_name": "Mama"}).json()

    r = put(client_a, f"/workspaces/{ws_a.id}/persons/{mama['id']}", {"display_name": "PAPA"})

    assert r.status_code == 409
    assert r.json()["code"] == "person_exists"


def test_delete_person(client_a: Client, ws_a: Workspace) -> None:
    person = create(client_a, ws_a.id, {"display_name": "Oma"}).json()

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/persons/{person['id']}")

    assert r.status_code == 204
    with workspace_context(ws_a.id):
        assert not Person.objects.filter(id=person["id"]).exists()


def test_setup_creates_the_admins_person(client: Client, settings: "Settings") -> None:
    settings.BRIEVIE_SETUP_TOKEN = SETUP_TOKEN
    r = post_json(
        client,
        "/setup",
        {
            "setup_token": SETUP_TOKEN,
            "email": "beheer@example.nl",
            "password": "een-lang-wachtwoord-123",
            "name": "Stijn",
            "workspace_name": "Thuis",
            "workspace_kind": "home",
        },
    )
    assert r.status_code == 201, r.json()
    workspace = Workspace.objects.get()
    admin = admin_of(workspace)

    r = client.get(persons_url(workspace.id))

    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["user_id"] == str(admin.id)
    assert r.json()[0]["display_name"] == "Stijn"
    assert r.json()[0]["name_variants"] == ["Stijn"]


def test_link_and_unlink_member(client_a: Client, ws_a: Workspace) -> None:
    member = join(ws_a, "member")
    first = create(client_a, ws_a.id, {"display_name": "Kind"}).json()
    second = create(client_a, ws_a.id, {"display_name": "Kind 2"}).json()
    base = f"/workspaces/{ws_a.id}/persons"

    r = put(client_a, f"{base}/{first['id']}/member", {"user_id": str(member.id)})
    assert r.status_code == 200
    assert r.json()["user_id"] == str(member.id)

    r = put(client_a, f"{base}/{second['id']}/member", {"user_id": str(member.id)})
    assert r.status_code == 409
    assert r.json()["code"] == "member_already_linked"

    r = put(client_a, f"{base}/{first['id']}/member", {})
    assert r.status_code == 200
    assert r.json()["user_id"] is None

    r = put(client_a, f"{base}/{second['id']}/member", {"user_id": str(member.id)})
    assert r.status_code == 200


def test_link_to_a_non_member_is_404(client_a: Client, ws_a: Workspace, ws_b: Workspace) -> None:
    person = create(client_a, ws_a.id, {"display_name": "Kind"}).json()
    base = f"/workspaces/{ws_a.id}/persons/{person['id']}/member"

    for user_id in (admin_of(ws_b).id, uuid.uuid4()):
        r = put(client_a, base, {"user_id": str(user_id)})
        assert r.status_code == 404
        assert r.json()["code"] == "member_not_found"


def test_delete_linked_person_is_refused(client_a: Client, ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        own = Person.objects.get(user=admin_of(ws_a))

    r = client_a.delete(f"/api/v1/workspaces/{ws_a.id}/persons/{own.id}")

    assert r.status_code == 409
    assert r.json()["code"] == "person_in_use"
    with workspace_context(ws_a.id):
        assert Person.objects.filter(id=own.id).exists()


def test_viewer_reads_but_cannot_create(client: Client, ws_a: Workspace) -> None:
    client.force_login(join(ws_a, "viewer"))

    assert client.get(persons_url(ws_a.id)).status_code == 200
    r = create(client, ws_a.id, {"display_name": "Oma"})

    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_member_cannot_link(client: Client, ws_a: Workspace) -> None:
    member = join(ws_a, "member")
    client.force_login(member)
    person = create(client, ws_a.id, {"display_name": "Oma"}).json()

    r = put(
        client,
        f"/workspaces/{ws_a.id}/persons/{person['id']}/member",
        {"user_id": str(member.id)},
    )

    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_unknown_person_is_404(client_a: Client, ws_a: Workspace) -> None:
    path = f"/workspaces/{ws_a.id}/persons/{uuid.uuid4()}"

    for r in (
        put(client_a, path, {"display_name": "X"}),
        client_a.delete(f"/api/v1{path}"),
        put(client_a, f"{path}/member", {}),
    ):
        assert r.status_code == 404
        assert r.json()["code"] == "person_not_found"


def test_other_workspace_is_404_on_every_person_endpoint(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    with workspace_context(ws_b.id):
        other = Person.objects.get(user=admin_of(ws_b))

    # A person of workspace B through workspace A's URL is not found.
    path_a = f"/workspaces/{ws_a.id}/persons/{other.id}"
    for r in (
        put(client_a, path_a, {"display_name": "X"}),
        client_a.delete(f"/api/v1{path_a}"),
        put(client_a, f"{path_a}/member", {}),
    ):
        assert r.status_code == 404
        assert r.json()["code"] == "person_not_found"

    # Workspace B itself answers like a missing workspace.
    path_b = f"/workspaces/{ws_b.id}/persons/{other.id}"
    for r in (
        client_a.get(persons_url(ws_b.id)),
        create(client_a, ws_b.id, {"display_name": "X"}),
        put(client_a, path_b, {"display_name": "X"}),
        client_a.delete(f"/api/v1{path_b}"),
        put(client_a, f"{path_b}/member", {}),
    ):
        assert r.status_code == 404
        assert r.json()["code"] == "workspace_not_found"
    with workspace_context(ws_b.id):
        other.refresh_from_db()
        assert other.user_id == admin_of(ws_b).id

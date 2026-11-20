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


def test_names_are_tidied_and_compared_as_matching_compares_them(
    client_a: Client, ws_a: Workspace
) -> None:
    # The finding: "Test  Persoon" (two spaces) was accepted next to "Test Persoon".
    assert create(client_a, ws_a.id, {"display_name": "Test Persoon"}).status_code == 201

    r = create(client_a, ws_a.id, {"display_name": "Test  Persoon"})
    assert r.status_code == 409
    assert r.json()["code"] == "person_exists"

    # Decomposed accents become one character; whitespace runs one space.
    r = create(
        client_a,
        ws_a.id,
        {"display_name": "Rene\u0301\t Jansen", "name_variants": ["R.  Jansen", "r. jansen"]},
    )
    assert r.status_code == 201
    assert (r.json()["display_name"], r.json()["name_variants"]) == ("René Jansen", ["R. Jansen"])


def test_a_name_another_active_person_has_is_refused(client_a: Client, ws_a: Workspace) -> None:
    stijn = create(client_a, ws_a.id, {"display_name": "Stijn", "name_variants": ["S. van de Pol"]})
    assert stijn.status_code == 201

    bodies: list[dict[str, object]] = [
        {"display_name": "Andere Stijn", "name_variants": ["s.  VAN DE POL"]},
        {"display_name": "Andere Stijn", "name_variants": ["Stijn"]},
        {"display_name": "S. van de Pol"},
    ]
    for body in bodies:
        r = create(client_a, ws_a.id, body)
        assert r.status_code == 409, body
        assert r.json()["code"] == "name_variant_taken"
        assert "hoort al bij Stijn." in r.json()["message"]

    # An archived person is not matched, so its names do not count...
    path = f"/workspaces/{ws_a.id}/persons/{stijn.json()['id']}"
    archived = put(
        client_a,
        path,
        {"display_name": "Stijn", "name_variants": ["S. van de Pol"], "archived": True},
    )
    assert archived.status_code == 200
    andere = create(
        client_a, ws_a.id, {"display_name": "Andere", "name_variants": ["S. van de Pol"]}
    )
    assert andere.status_code == 201
    # ...until it comes back.
    back = put(
        client_a,
        path,
        {"display_name": "Stijn", "name_variants": ["S. van de Pol"], "archived": False},
    )
    assert back.status_code == 409
    assert back.json()["message"].startswith("De naam 'S. van de Pol' hoort al bij Andere.")


def test_too_many_variants_are_refused(client_a: Client, ws_a: Workspace) -> None:
    variants = [f"Naam {i}" for i in range(21)]

    r = create(client_a, ws_a.id, {"display_name": "Jan", "name_variants": variants})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_blank_display_name_is_422(client_a: Client, ws_a: Workspace) -> None:
    r = create(client_a, ws_a.id, {"display_name": "   "})

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


BSN_REFUSED = "Een BSN bewaren we nooit"


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": "Jan 111222333"},
        {"display_name": "Jan", "name_variants": ["123456782"]},
        {"display_name": "Jan", "name_variants": ["J 111.222.333"]},
        # The token a BSN became: it would find every letter that held any BSN.
        {"display_name": "Jan", "name_variants": ["[BSN]"]},
    ],
)
def test_a_bsn_in_a_name_is_refused(
    client_a: Client, ws_a: Workspace, body: dict[str, object]
) -> None:
    r = create(client_a, ws_a.id, body)

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert r.json()["message"].startswith(BSN_REFUSED)
    with workspace_context(ws_a.id):
        assert not Person.objects.filter(display_name__startswith="Jan").exists()


def test_a_person_with_a_masked_variant_from_before_stays_editable(
    client_a: Client, ws_a: Workspace
) -> None:
    # Stored before BSNs were refused; it matches nothing now (common.text).
    with workspace_context(ws_a.id):
        old = Person.objects.create(
            workspace=ws_a, display_name="Jan [BSN]", name_variants=["[BSN]", "J. Jansen"]
        )
    path = f"/workspaces/{ws_a.id}/persons/{old.id}"

    kept = put(client_a, path, {"display_name": "Jan [BSN]", "name_variants": ["[BSN]"]})
    assert kept.status_code == 200
    assert kept.json()["name_variants"] == ["[BSN]"]

    added = put(client_a, path, {"display_name": "Jan [BSN]", "name_variants": ["999999990"]})
    assert added.status_code == 422
    assert added.json()["message"].startswith(BSN_REFUSED)


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


def test_a_rename_keeps_what_it_leaves_out(client_a: Client, ws_a: Workspace) -> None:
    # The finding: a rename without `archived` took the person out of the archive.
    r = create(
        client_a,
        ws_a.id,
        {"display_name": "Test Persoon", "name_variants": ["T. Persoon"], "archived": True},
    )
    path = f"/workspaces/{ws_a.id}/persons/{r.json()['id']}"

    renamed = put(client_a, path, {"display_name": "Test Persoon B"})

    assert renamed.status_code == 200
    out = renamed.json()
    assert (out["display_name"], out["name_variants"], out["archived"]) == (
        "Test Persoon B",
        ["T. Persoon"],
        True,
    )
    restored = put(client_a, path, {"display_name": "Test Persoon B", "archived": False})
    assert restored.json()["archived"] is False
    cleared = put(client_a, path, {"display_name": "Test Persoon B", "name_variants": []})
    assert (cleared.json()["name_variants"], cleared.json()["archived"]) == ([], False)


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
            "password": "een-lang-wachtwoord-2026",
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

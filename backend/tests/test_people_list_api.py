"""`listPeople` and `getPersonHasPrivatePost` (spec 6, 8; plan Task 4)."""

import json
from pathlib import Path

import pytest
from django.test import Client
from pytest_django import Settings

from apps.workspaces.models import Workspace
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.members_support import as_user, bearer, paired
from tests.visibility_support import Home, home, office, private_letter

ACCOUNT_FIELDS = (
    "email",
    "last_sign_in_at",
    "last_app_use_at",
    "deactivated_at",
    "invitation_id",
    "invitation_expires_at",
)
CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.json"


@pytest.fixture(autouse=True)
def _public_url(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _url(workspace: Workspace, query: str = "") -> str:
    return f"/api/v1/workspaces/{workspace.id}/people{query}"


def _by_name(client: Client, h: Home, query: str = "") -> dict[str, dict[str, object]]:
    r = client.get(_url(h.workspace, query))
    assert r.status_code == 200, r.json()
    return {row["display_name"]: row for row in r.json()}


def _invite_oma(h: Home) -> None:
    r = post_json(
        as_user(h.anne),
        f"/workspaces/{h.workspace.id}/invitations",
        {"person_id": str(h.persons["Oma"].id)},
    )
    assert r.status_code == 201, r.json()


def test_admin_gets_people_with_account_columns() -> None:
    h = home()
    _invite_oma(h)
    people = _by_name(as_user(h.anne), h)
    assert set(people) == {"Anne", "Bob", "Carla", "Oma"}
    assert people["Bob"]["state"] == "active"
    assert people["Bob"]["email"] == h.bob.email
    assert people["Bob"]["role"] == "member"
    assert people["Bob"]["user_id"] == str(h.bob.id)
    assert people["Oma"]["state"] == "invited"
    assert people["Oma"]["invitation_id"] is not None
    assert people["Oma"]["invitation_expired"] is False
    assert people["Oma"]["invitation_role"] == "member"
    assert people["Anne"]["can_manage_account"] is False  # never one's own account


def test_member_gets_people_without_account_columns() -> None:
    h = home()
    _invite_oma(h)
    people = _by_name(as_user(h.bob), h)
    assert people["Oma"]["state"] == "inactive"
    for row in people.values():
        for name in ACCOUNT_FIELDS:
            assert row[name] in ("", None), (row["display_name"], name)
        assert row["can_manage_account"] is False
        assert row["role"] == ""
        assert row["invitation_role"] == ""


def test_device_token_gets_401() -> None:
    h = home()
    _device, token = paired(h.anne)
    r = Client().get(_url(h.workspace), headers=bearer(token))
    assert r.status_code == 401
    r = Client().get(
        f"/api/v1/workspaces/{h.workspace.id}/persons/{h.persons['Oma'].id}/private-post",
        headers=bearer(token),
    )
    assert r.status_code == 401


def test_other_workspace_is_404() -> None:
    h = home()
    stranger = UserFactory.create()
    r = as_user(stranger).get(_url(h.workspace))
    assert (r.status_code, r.json()["code"]) == (404, "workspace_not_found")


def test_state_filter() -> None:
    h = home()
    _invite_oma(h)
    admin = as_user(h.anne)
    assert set(_by_name(admin, h, "?state=invited")) == {"Oma"}
    assert set(_by_name(admin, h, "?state=active")) == {"Anne", "Bob", "Carla"}
    assert set(_by_name(admin, h, "?state=inactive")) == set()
    assert set(_by_name(as_user(h.bob), h, "?state=inactive")) == {"Oma"}


def test_unknown_state_is_422() -> None:
    h = home()
    for client, query in ((as_user(h.anne), "?state=gone"), (as_user(h.bob), "?state=invited")):
        r = client.get(_url(h.workspace, query))
        assert (r.status_code, r.json()["code"]) == (422, "validation_error")


def test_departments_are_not_listed() -> None:
    o = office()
    r = as_user(o.admin).get(_url(o.workspace))
    assert r.status_code == 200
    names = {row["display_name"] for row in r.json()}
    assert not names & {"Finance", "HR", "Directie"}


def test_schema_has_no_anyof() -> None:
    contract = json.loads(CONTRACT.read_text())
    for name in ("PersonAccountOut", "PersonPrivatePostOut"):
        assert "anyOf" not in json.dumps(contract["components"]["schemas"][name])


def test_private_post_read_admin_only() -> None:
    o = office()
    private_letter(o.workspace, addressees=[o.persons["Hans"]], scanned_by=o.sorter)
    base = f"/api/v1/workspaces/{o.workspace.id}/persons"
    admin = as_user(o.admin)
    r = admin.get(f"{base}/{o.persons['Hans'].id}/private-post")
    assert (r.status_code, r.json()) == (200, {"has_private_post": True})
    r = admin.get(f"{base}/{o.persons['Piet'].id}/private-post")
    assert r.json() == {"has_private_post": False}
    r = as_user(o.plain).get(f"{base}/{o.persons['Hans'].id}/private-post")
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    r = admin.get(f"{base}/{o.persons['Finance'].id}/private-post")
    assert (r.status_code, r.json()["code"]) == (404, "person_not_found")


def test_list_people_has_no_private_post_field() -> None:
    """A list column would show an admin at a glance who has private letters (spec 4)."""
    contract = json.loads(CONTRACT.read_text())
    properties = contract["components"]["schemas"]["PersonAccountOut"]["properties"]
    assert not [name for name in properties if "private" in name]

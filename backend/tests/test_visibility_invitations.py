"""An invitation moves sight like any member change (security review of PR #39,
finding 1): an admin invitation that widens sight is previewed and confirmed, joining
tells who gains or loses, and the sorters' assignments on a person's post end at once."""

import re

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import AccountNotice, User
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json
from tests.visibility_support import Office, as_actor, office

LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _invite(client: Client, o: Office, **body: object) -> "tuple[int, dict[str, object]]":
    r = post_json(client, f"/workspaces/{o.workspace.id}/invitations", body)
    return r.status_code, r.json()


def _token_of(body: dict[str, object]) -> str:
    match = LINK.search(str(body["link"]))
    assert match is not None
    return match["token"]


def _accept(token: str, email: str, *, name: str = "Nina") -> User:
    """Accept with an existing account of that address (made here, in no workspace)."""
    user = UserFactory.create(email=email, name=name)
    r = post_json(
        Client(),
        "/invitations/accept",
        {
            "token": token,
            "account": "existing",
            "name": "",
            "email": email,
            "password": TEST_PASSWORD,
        },
    )
    assert r.status_code == 200, r.json()
    return user


def _preview_invite(client: Client, o: Office) -> dict[str, object]:
    r = post_json(
        client,
        f"/workspaces/{o.workspace.id}/visibility/preview",
        {"member_change": "invite_admin"},
    )
    assert r.status_code == 200, r.json()
    body: dict[str, object] = r.json()
    return body


def test_an_admin_invitation_that_widens_sight_needs_confirmation(client: Client) -> None:
    o = office()
    o.state(e=False, d=True, a=True)
    client.force_login(o.admin)
    status, body = _invite(client, o, role="admin", email="nina@example.nl")
    assert status == 409
    assert body["code"] == "confirmation_required"

    preview = _preview_invite(client, o)
    assert preview["needs_confirmation"] is True
    changes = preview["changes"]
    assert isinstance(changes, list)
    assert [c["gains"] for c in changes] == [["alle post als beheerder"]]
    # Nothing of the preview stays: no placeholder account, no membership.
    assert not User.objects.filter(email__endswith="@preview.invalid").exists()
    assert Membership.objects.filter(workspace=o.workspace).count() == 6

    status, body = _invite(
        client,
        o,
        role="admin",
        email="nina@example.nl",
        confirmed=True,
        preview_token=preview["preview_token"],
    )
    assert status == 201, body


def test_a_member_invitation_and_a_narrow_admin_invitation_need_none(client: Client) -> None:
    o = office()  # A off, Sandra sorts: a new admin gains nothing
    client.force_login(o.admin)
    assert _invite(client, o, role="admin", email="a@example.nl")[0] == 201
    o.state(e=False, d=True, a=True)
    assert _invite(client, o, role="member", email="b@example.nl")[0] == 201


def test_a_token_for_another_change_does_not_confirm_an_admin_invitation(
    client: Client,
) -> None:
    o = office()
    o.state(e=False, d=True, a=True)
    client.force_login(o.admin)
    other = post_json(
        client, f"/workspaces/{o.workspace.id}/visibility/preview", {"admins_read_all": False}
    ).json()["preview_token"]
    status, body = _invite(
        client, o, role="admin", email="nina@example.nl", confirmed=True, preview_token=other
    )
    assert status == 409
    assert body["code"] == "preview_outdated"


def test_accepting_an_admin_invitation_tells_every_member(client: Client) -> None:
    o = office()
    o.state(e=False, d=True, a=True)
    client.force_login(o.admin)
    preview = _preview_invite(client, o)
    status, body = _invite(
        client,
        o,
        role="admin",
        email="nina@example.nl",
        confirmed=True,
        preview_token=preview["preview_token"],
    )
    assert status == 201, body
    nina = _accept(_token_of(body), "nina@example.nl")
    for user in o.users.values():
        assert AccountNotice.objects.filter(user=user, code="admins_read_all_on").exists(), user
    assert not AccountNotice.objects.filter(user=nina).exists()


def test_accepting_an_invitation_for_a_person_ends_the_sorters_assignments(
    client: Client,
) -> None:
    o = office()
    jan = o.persons["Jan"]
    letter = o.letters["person_without_account"]
    with as_actor(o.workspace, SYSTEM):
        task = Task.objects.get(document=letter)
        Task.objects.filter(id=task.id).update(assigned_to=o.sorter, assigned_by=o.sorter)
    client.force_login(o.admin)
    status, body = _invite(client, o, role="member", email="jan@example.nl", person_id=str(jan.id))
    assert status == 201, body
    _accept(_token_of(body), "jan@example.nl", name="Jan")
    assert "person_without_account" not in o.visible(o.sorter.id)
    with as_actor(o.workspace, SYSTEM):
        assert Task.objects.get(id=task.id).assigned_to_id is None
    # Sandra hears that Jan's post is no longer hers to sort.
    assert AccountNotice.objects.filter(
        user=o.sorter, code="sight_narrowed", values__person="Jan"
    ).exists()

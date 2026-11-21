"""An invitation moves sight like any member change (security review of PR #39,
finding 1): an admin invitation that widens sight is previewed and confirmed, joining
tells who gains or loses, and the sorters' assignments on a person's post end at once."""

import re
import uuid

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import AccountNotice, User
from apps.people.models import Person
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Invitation, Membership
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json
from tests.visibility_support import Office, as_actor, home, office

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


def _preview_person_invite(
    client: Client, o: Office, person_id: uuid.UUID, role: str = "member"
) -> dict[str, object]:
    r = post_json(
        client,
        f"/workspaces/{o.workspace.id}/visibility/preview",
        {"member_change": "invite", "member_role": role, "member_person_id": str(person_id)},
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
    preview = _preview_person_invite(client, o, jan.id)
    status, body = _invite(
        client,
        o,
        role="member",
        email="jan@example.nl",
        person_id=str(jan.id),
        confirmed=True,
        preview_token=preview["preview_token"],
    )
    assert status == 201, body
    _accept(_token_of(body), "jan@example.nl", name="Jan")
    assert "person_without_account" not in o.visible(o.sorter.id)
    with as_actor(o.workspace, SYSTEM):
        assert Task.objects.get(id=task.id).assigned_to_id is None
    # Sandra hears that Jan's post is no longer hers to sort.
    assert AccountNotice.objects.filter(
        user=o.sorter, code="sight_narrowed", values__person="Jan"
    ).exists()


@pytest.mark.parametrize(
    ("state", "widens"),
    [
        ({"e": True, "d": False, "a": True, "s": False}, False),  # Thuis: everyone sees all
        ({"e": False, "d": True, "a": False, "s": False}, True),  # a fallback sorter more
        ({"e": False, "d": True, "a": True, "s": False}, True),  # admins read all post
        ({"e": False, "d": True, "a": False, "s": True}, False),  # Sandra sorts
    ],
)
def test_the_preview_and_the_invitation_agree(
    client: Client, state: dict[str, bool], widens: bool
) -> None:
    """Only what the admin role adds beyond a member invitation counts."""
    o = office()
    o.state(**state)
    client.force_login(o.admin)
    preview = _preview_invite(client, o)
    assert preview["needs_confirmation"] is widens
    changes = preview["changes"]
    assert isinstance(changes, list)
    assert {c["name"] for c in changes} <= {"Nieuwe beheerder"}
    status, body = _invite(client, o, role="admin", email="nina@example.nl")
    assert status == (409 if widens else 201), body


# --- An invitation for a person counts as confirmed when it is made (S 4.4) -------------


def test_an_invitation_for_a_person_needs_confirmation_in_own_post(client: Client) -> None:
    """S 4.4, "person linked to a member": confirm "yes (... an accepted invitation was
    confirmed when it was made)". With own post the newcomer gains the person's post."""
    o = office()
    jan = o.persons["Jan"]
    client.force_login(o.admin)
    status, body = _invite(client, o, role="member", person_id=str(jan.id))
    assert status == 409
    assert body["code"] == "confirmation_required"

    preview = _preview_person_invite(client, o, jan.id)
    assert preview["needs_confirmation"] is True
    changes = preview["changes"]
    assert isinstance(changes, list)
    gains = {c["name"]: c["gains"] for c in changes}
    assert gains["Nieuw lid"] == ["post van Jan"]
    # Sandra stops sorting Jan's post: she is listed as losing it.
    assert any("post van Jan" in c["losses"] for c in changes if c["name"] != "Nieuw lid")
    # Nothing of the preview stays: no placeholder, no link.
    assert not User.objects.filter(email__endswith="@preview.invalid").exists()
    with as_actor(o.workspace, SYSTEM):
        jan.refresh_from_db()
    assert jan.user_id is None

    status, body = _invite(
        client,
        o,
        role="member",
        person_id=str(jan.id),
        confirmed=True,
        preview_token=preview["preview_token"],
    )
    assert status == 201, body


def test_a_token_for_another_person_does_not_confirm(client: Client) -> None:
    o = office()
    client.force_login(o.admin)
    with as_actor(o.workspace, SYSTEM):
        other = Person.objects.create(workspace=o.workspace, display_name="Kees")
    token = _preview_person_invite(client, o, other.id)["preview_token"]
    status, body = _invite(
        client,
        o,
        role="member",
        person_id=str(o.persons["Jan"].id),
        confirmed=True,
        preview_token=token,
    )
    assert status == 409
    assert body["code"] == "preview_outdated"
    # Nor does a member preview confirm the same person as an admin.
    token = _preview_person_invite(client, o, o.persons["Jan"].id)["preview_token"]
    o.state(e=False, d=True, a=True)
    status, body = _invite(
        client,
        o,
        role="admin",
        person_id=str(o.persons["Jan"].id),
        confirmed=True,
        preview_token=token,
    )
    assert status == 409
    assert body["code"] == "preview_outdated"


def test_a_person_invitation_in_thuis_asks_nothing(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    r = post_json(
        client,
        f"/workspaces/{h.workspace.id}/invitations",
        {"person_id": str(h.persons["Oma"].id), "email": "oma@example.nl"},
    )
    assert r.status_code == 201, r.json()
    r = post_json(
        client,
        f"/workspaces/{h.workspace.id}/visibility/preview",
        {
            "member_change": "invite",
            "member_role": "member",
            "member_person_id": str(h.persons["Oma"].id),
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["needs_confirmation"] is False


def test_previewing_an_invitation_for_a_linked_person_answers_the_refusal(
    client: Client,
) -> None:
    o = office()
    client.force_login(o.admin)
    piet = o.persons["Piet"]  # linked to a member
    r = post_json(
        client,
        f"/workspaces/{o.workspace.id}/visibility/preview",
        {"member_change": "invite", "member_role": "member", "member_person_id": str(piet.id)},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "person_already_linked"
    status, body = _invite(client, o, role="member", person_id=str(piet.id))
    assert (status, body["code"]) == (409, "person_already_linked")


# --- Renewing asks again when the renewal widens sight at that moment -----------------


def _renew(
    client: Client, o: Office, invitation_id: object, **body: object
) -> "tuple[int, dict[str, object]]":
    path = f"/workspaces/{o.workspace.id}/invitations/{invitation_id}/renew"
    r = post_json(client, path, body if body else None)
    return r.status_code, r.json()


def test_renewing_an_admin_invitation_asks_again_when_it_now_widens(client: Client) -> None:
    o = office()  # A off, Sandra sorts: an admin invitation widens nothing
    client.force_login(o.admin)
    status, body = _invite(client, o, role="admin", email="nina@example.nl")
    assert status == 201, body
    invitation = Invitation.objects.get(workspace=o.workspace, revoked_at=None)
    o.state(e=False, d=True, a=True)  # now admins read all post

    status, body = _renew(client, o, invitation.id)
    assert status == 409
    assert body["code"] == "confirmation_required"
    # Refused: the old link still works.
    invitation.refresh_from_db()
    assert invitation.revoked_at is None

    preview = _preview_invite(client, o)
    status, body = _renew(
        client, o, invitation.id, confirmed=True, preview_token=preview["preview_token"]
    )
    assert status == 201, body
    invitation.refresh_from_db()
    assert invitation.revoked_at is not None


def test_renewing_a_person_invitation_asks_again(client: Client) -> None:
    o = office()
    jan = o.persons["Jan"]
    client.force_login(o.admin)
    preview = _preview_person_invite(client, o, jan.id)
    status, body = _invite(
        client,
        o,
        person_id=str(jan.id),
        confirmed=True,
        preview_token=preview["preview_token"],
    )
    assert status == 201, body
    invitation = Invitation.objects.get(workspace=o.workspace, revoked_at=None)
    assert _renew(client, o, invitation.id)[1]["code"] == "confirmation_required"
    preview = _preview_person_invite(client, o, jan.id)
    status, body = _renew(
        client, o, invitation.id, confirmed=True, preview_token=preview["preview_token"]
    )
    assert status == 201, body


def test_renewing_an_invitation_that_widens_nothing_needs_no_body(client: Client) -> None:
    o = office()
    client.force_login(o.admin)
    status, body = _invite(client, o, role="member", email="b@example.nl")
    assert status == 201, body
    invitation = Invitation.objects.get(workspace=o.workspace, revoked_at=None)
    status, body = _renew(client, o, invitation.id)
    assert status == 201, body

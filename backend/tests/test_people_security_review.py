"""The findings of the security review of PR #43 (Mensen), one test per path."""

import re
import uuid
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User
from apps.audit.models import WorkspaceAuditEvent
from apps.documents.models import MemberExport
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Invitation, Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json
from tests.members_support import as_user
from tests.visibility_support import Home, Office, as_actor, home, office, private_letter

LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _invite(h: Home, person: Person, **body: object) -> str:
    r = post_json(
        as_user(h.anne),
        f"/workspaces/{h.workspace.id}/invitations",
        {"person_id": str(person.id), **body},
    )
    assert r.status_code == 201, r.json()
    match = LINK.search(r.json()["link"])
    assert match is not None
    return match["token"]


def _raw_invitation(
    workspace: Workspace, person: Person, maker: User, email: str = ""
) -> Invitation:
    """An open invitation written directly: what an earlier release, or a race, left."""
    return Invitation.objects.create(
        workspace=workspace,
        role="member",
        person=person,
        email=email,
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        expires_at=timezone.now() + timedelta(days=7),
        created_by=maker,
        created_by_name=maker.name,
    )


def _accept(token: str, email: str, account: str = "new") -> "tuple[int, dict[str, object]]":
    r = post_json(
        Client(),
        "/invitations/accept",
        {
            "token": token,
            "account": account,
            "name": "X",
            "email": email,
            "password": TEST_PASSWORD,
        },
    )
    return r.status_code, r.json()


def _open(invitation: Invitation) -> bool:
    invitation.refresh_from_db()
    return invitation.revoked_at is None and invitation.accepted_at is None


# --- point 1: one way in per person ------------------------------------------------------


def test_a_new_invitation_for_a_person_replaces_the_earlier_one() -> None:
    h = home()
    _invite(h, h.persons["Oma"])
    first = Invitation.objects.get(person_id=h.persons["Oma"].id)
    _invite(h, h.persons["Oma"], email="oma@example.nl")
    assert not _open(first)
    assert (
        Invitation.objects.filter(
            person_id=h.persons["Oma"].id, accepted_at=None, revoked_at=None
        ).count()
        == 1
    )
    with as_actor(h.workspace, SYSTEM):
        assert WorkspaceAuditEvent.objects.filter(
            code="invitation_revoked", values__reason="replaced"
        ).exists()


def test_accepting_stops_every_other_link_for_the_person() -> None:
    h = home()
    token = _invite(h, h.persons["Oma"])
    other = _raw_invitation(h.workspace, h.persons["Oma"], h.anne, email="tante@example.nl")
    status, body = _accept(token, "oma@example.nl")
    assert status == 200, body
    assert not _open(other)


def test_switching_off_stops_every_link_for_the_person() -> None:
    h = home()
    old = _raw_invitation(h.workspace, h.persons["Bob"], h.anne, email="iemand@example.nl")
    r = as_user(h.anne).delete(f"/api/v1/workspaces/{h.workspace.id}/members/{h.bob.id}")
    assert r.status_code == 204, r.content
    assert not _open(old)


def test_unlinking_stops_every_link_for_the_person() -> None:
    h = home()
    old = _raw_invitation(h.workspace, h.persons["Bob"], h.anne)
    r = as_user(h.bob).put(
        f"/api/v1/workspaces/{h.workspace.id}/persons/{h.persons['Bob'].id}/member",
        {"user_id": None},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    assert not _open(old)


def test_a_link_without_address_opens_no_private_post_that_came_later() -> None:
    h = home()
    token = _invite(h, h.persons["Oma"])  # no private post yet: no address needed
    private_letter(h.workspace, addressees=[h.persons["Oma"]], scanned_by=h.bob)
    status, body = _accept(token, "wie@example.nl")
    assert (status, body["code"]) == (400, "invitation_invalid")
    with as_actor(h.workspace, SYSTEM):
        assert Person.objects.get(id=h.persons["Oma"].id).user_id is None
    assert not User.objects.filter(email="wie@example.nl").exists()


def test_an_open_link_shows_on_the_row_of_an_active_person() -> None:
    h = home()
    old = _raw_invitation(h.workspace, h.persons["Bob"], h.anne)
    rows = as_user(h.anne).get(f"/api/v1/workspaces/{h.workspace.id}/people").json()
    bob = next(r for r in rows if r["display_name"] == "Bob")
    assert (bob["state"], bob["invitation_id"]) == ("active", str(old.id))


# --- point 2: no activity from elsewhere ---------------------------------------------------


def test_activity_of_an_account_in_another_workspace_is_not_shown(client: Client) -> None:
    h = home()
    elsewhere = create_workspace(name="Werk", kind="business", admin=UserFactory.create())
    Membership.objects.create(user=h.bob, workspace=elsewhere, role="member")
    r = post_json(client, "/auth/login", {"email": h.bob.email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.json()
    rows = as_user(h.anne).get(f"/api/v1/workspaces/{h.workspace.id}/people").json()
    bob = next(r for r in rows if r["display_name"] == "Bob")
    assert (bob["last_sign_in_at"], bob["last_app_use_at"], bob["activity_hidden"]) == (
        None,
        None,
        True,
    )


def test_a_switched_off_person_shows_the_activity_frozen_at_switch_off(client: Client) -> None:
    h = home()
    elsewhere = create_workspace(name="Werk", kind="business", admin=UserFactory.create())
    Membership.objects.create(user=h.bob, workspace=elsewhere, role="member")
    r = as_user(h.anne).delete(f"/api/v1/workspaces/{h.workspace.id}/members/{h.bob.id}")
    assert r.status_code == 204, r.content
    # Bob signs in afterwards, for the other workspace.
    r = post_json(client, "/auth/login", {"email": h.bob.email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.json()
    rows = as_user(h.anne).get(f"/api/v1/workspaces/{h.workspace.id}/people").json()
    bob = next(r for r in rows if r["display_name"] == "Bob")
    assert bob["state"] == "deactivated"
    assert bob["last_sign_in_at"] is None  # never signed in before the switch-off
    assert bob["email"] == h.bob.email


# --- point 4: managing private post is agreed to apart ------------------------------------


def _preview_token(admin: Client, o: Office, person: str, user_id: uuid.UUID) -> str:
    r = post_json(
        admin,
        f"/workspaces/{o.workspace.id}/visibility/preview",
        {
            "member_change": "manage",
            "member_user_id": str(user_id),
            "member_person_id": str(o.persons[person].id),
        },
    )
    assert r.status_code == 200, r.json()
    return str(r.json()["preview_token"])


def test_managing_private_post_needs_its_own_yes() -> None:
    o = office()
    private_letter(o.workspace, addressees=[o.persons["Jan"]], scanned_by=o.sorter)
    admin = as_user(o.admin)
    url = f"/api/v1/workspaces/{o.workspace.id}/persons/{o.persons['Jan'].id}/manager"
    token = _preview_token(admin, o, "Jan", o.hr.id)
    body = {"user_id": str(o.hr.id), "confirmed": True, "preview_token": token}
    r = admin.put(url, body, content_type="application/json")
    assert (r.status_code, r.json()["code"]) == (409, "private_post_acknowledgement_required")
    r = admin.put(url, {**body, "private_post_acknowledged": True}, content_type="application/json")
    assert r.status_code == 200, r.json()


# --- point 5: losing sight ends one's own export ------------------------------------------


def test_the_sorters_export_goes_when_management_starts() -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        MemberExport.objects.create(
            workspace=o.workspace, user=o.sorter, storage_path=f"exports/{uuid.uuid4()}"
        )
    admin = as_user(o.admin)
    token = _preview_token(admin, o, "Jan", o.plain.id)
    r = admin.put(
        f"/api/v1/workspaces/{o.workspace.id}/persons/{o.persons['Jan'].id}/manager",
        {"user_id": str(o.plain.id), "confirmed": True, "preview_token": token},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    with as_actor(o.workspace, SYSTEM):
        assert not MemberExport.objects.filter(user=o.sorter).exists()


# --- point 6: the private-post answer only for switching an account off --------------------


def test_private_post_answer_only_for_an_active_account() -> None:
    o = office()
    admin = as_user(o.admin)
    base = f"/api/v1/workspaces/{o.workspace.id}/persons"
    r = admin.get(f"{base}/{o.persons['Jan'].id}/private-post")
    assert (r.status_code, r.json()["code"]) == (409, "person_has_no_account")
    r = admin.get(f"{base}/{o.persons['Piet'].id}/private-post")
    assert r.status_code == 200

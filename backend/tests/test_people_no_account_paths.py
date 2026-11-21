"""A person without an active account can do nothing an account does (spec 6, M1, M3;
plan Task 5): there is no `User` to sign in, reset, pair or list."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User, UserSession
from apps.people.models import Person
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Invitation
from tests.factories import TEST_PASSWORD
from tests.helpers import post_json
from tests.members_support import as_user, bearer, paired
from tests.visibility_support import Home, as_actor, home

APPS = Path(__file__).resolve().parent.parent / "apps"
LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _invite(h: Home, person: Person, email: str = "") -> str:
    r = post_json(
        as_user(h.anne),
        f"/workspaces/{h.workspace.id}/invitations",
        {"person_id": str(person.id), "email": email},
    )
    assert r.status_code == 201, r.json()
    match = LINK.search(r.json()["link"])
    assert match is not None
    return match["token"]


def _three_kinds(h: Home) -> dict[str, Person]:
    """A person never invited, one invited, and one whose account was switched off."""
    with as_actor(h.workspace, SYSTEM):
        opa = Person.objects.create(workspace=h.workspace, display_name="Opa")
    _invite(h, opa, email="opa@example.nl")
    r = as_user(h.anne).delete(f"/api/v1/workspaces/{h.workspace.id}/members/{h.bob.id}")
    assert r.status_code == 204, r.content
    return {"inactive": h.persons["Oma"], "invited": opa, "deactivated": h.persons["Bob"]}


def test_inactive_person_has_no_account_paths() -> None:
    h = home()
    users_before = User.objects.count()
    people = _three_kinds(h)
    # No account was made for anybody without one.
    assert User.objects.count() == users_before
    assert not User.objects.filter(email="opa@example.nl").exists()
    assert not User.objects.filter(name__in=["Oma", "Opa"]).exists()

    admin = as_user(h.anne)
    members = admin.get(f"/api/v1/workspaces/{h.workspace.id}/members").json()
    member_ids = {m["user_id"] for m in members}
    assert str(h.bob.id) not in member_ids
    for person in people.values():
        # The person's id is no account: every account action answers "not found".
        r = post_json(admin, f"/users/{person.id}/password-reset")
        assert (r.status_code, r.json()["code"]) == (404, "user_not_found")
        r = admin.get(f"/api/v1/users/{person.id}/devices")
        assert (r.status_code, r.json()["code"]) == (404, "user_not_found")
        r = admin.delete(f"/api/v1/users/{person.id}/sessions")
        assert (r.status_code, r.json()["code"]) == (404, "user_not_found")
        assert str(person.id) not in member_ids

    # The invitation's address signs nobody in: there is no account behind it.
    r = post_json(Client(), "/auth/login", {"email": "opa@example.nl", "password": TEST_PASSWORD})
    assert (r.status_code, r.json()["code"]) == (401, "invalid_credentials")

    rows = {
        row["display_name"]: row
        for row in admin.get(f"/api/v1/workspaces/{h.workspace.id}/people").json()
    }
    for name in ("Oma", "Opa", "Bob"):
        assert rows[name]["user_id"] is None


def test_switched_off_user_cannot_sign_in_or_pair() -> None:
    h = home()
    _device, token = paired(h.bob)
    assert Client().get("/api/v1/workspaces", headers=bearer(token)).status_code == 200
    r = as_user(h.anne).delete(f"/api/v1/workspaces/{h.workspace.id}/members/{h.bob.id}")
    assert r.status_code == 204, r.content
    bob = User.objects.get(id=h.bob.id)
    assert bob.is_active is False
    r = post_json(Client(), "/auth/login", {"email": bob.email, "password": TEST_PASSWORD})
    assert (r.status_code, r.json()["code"]) == (401, "invalid_credentials")
    # The phone paired before stops; no session of the account is left.
    assert Client().get("/api/v1/workspaces", headers=bearer(token)).status_code == 401
    assert not UserSession.objects.filter(user=bob).exists()


@pytest.mark.parametrize("dead", ["expired", "revoked"])
def test_invited_person_cannot_use_expired_or_revoked_link(dead: str) -> None:
    h = home()
    token = _invite(h, h.persons["Oma"])
    invitation = Invitation.objects.get(person_id=h.persons["Oma"].id)
    if dead == "expired":
        Invitation.objects.filter(id=invitation.id).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
    else:
        r = as_user(h.anne).delete(
            f"/api/v1/workspaces/{h.workspace.id}/invitations/{invitation.id}"
        )
        assert r.status_code == 204
    r = post_json(
        Client(),
        "/invitations/accept",
        {
            "token": token,
            "account": "new",
            "name": "Oma",
            "email": "oma@example.nl",
            "password": TEST_PASSWORD,
        },
    )
    assert (r.status_code, r.json()["code"]) == (400, "invitation_invalid")
    with as_actor(h.workspace, SYSTEM):
        assert Person.objects.get(id=h.persons["Oma"].id).user_id is None
    assert not User.objects.filter(email="oma@example.nl").exists()


# Where a person is given an account: accepting an invitation (`_link_person`), the old
# link endpoint (`link_member`) and a member's own new person (`ensure_person_for_member`,
# for a member who exists already). The preview links inside a savepoint it rolls back.
ACCOUNT_LINK_WRITERS = {
    "apps/workspaces/invitations.py",
    "apps/people/services.py",
    "apps/workspaces/visibility_settings.py",
}
_WRITES_USER = re.compile(
    r"person\.user(_id)? = (?!None)|Person\.objects\.filter\([^)]*\)\.update\(user=(?!None)"
    r"|Person\.objects\.create\("
)


def test_activation_only_through_invitation() -> None:
    """M3: no other code gives a person an account (no "activate" path)."""
    writers = {
        str(path.relative_to(APPS.parent))
        for path in APPS.rglob("*.py")
        if "migrations" not in path.parts and _WRITES_USER.search(path.read_text())
    }
    assert writers == ACCOUNT_LINK_WRITERS

"""A member's whole life over HTTP: invited, joins, pairs a phone by proof, is removed, is
invited back. Coming back reactivates the account, but never what the removal ended: the
old phone token and the old web session stay refused (Tasks 9, 12 and 14 together)."""

import re

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import User
from apps.workspaces.models import Membership, Workspace
from tests.helpers import post_json
from tests.members_support import admin_of, as_user, bearer
from tests.test_devices_pairing_proof import HOST, pair_by_proof

PASSWORD = "een-lang-wachtwoord-voor-mila-2026"
LINK = re.compile(r"#(?P<token>[A-Za-z0-9_-]{43})$")


@pytest.fixture(autouse=True)
def _public_host(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = [f"https://{HOST}"]
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def invite(admin: Client, ws: Workspace) -> str:
    r = post_json(admin, f"/workspaces/{ws.id}/invitations", {})
    assert r.status_code == 201, r.json()
    match = LINK.search(r.json()["link"])
    assert match is not None
    return match["token"]


def accept(browser: Client, token: str, account: str) -> int:
    body = {
        "token": token,
        "account": account,
        "name": "Mila",
        "email": "mila@example.nl",
        "password": PASSWORD,
    }
    return post_json(browser, "/invitations/accept", body).status_code


def test_a_member_comes_back_without_the_old_phone_or_session(ws_a: Workspace) -> None:
    admin = as_user(admin_of(ws_a))

    # Invited, joins with a new account (the browser is now signed in), pairs a phone.
    browser = Client()
    assert accept(browser, invite(admin, ws_a), "new") == 200
    _, _, paired = pair_by_proof(browser)
    assert paired.status_code == 201, paired.content
    old_token = paired.json()["token"]
    phone = Client()
    assert phone.get("/api/v1/workspaces", headers=bearer(old_token)).status_code == 200
    assert browser.get("/api/v1/me").status_code == 200
    mila = User.objects.get(email="mila@example.nl")

    # Removed: the account is off, the phone and the browser are refused.
    assert admin.delete(f"/api/v1/workspaces/{ws_a.id}/members/{mila.id}").status_code == 204
    r = phone.get("/api/v1/workspaces", headers=bearer(old_token))
    assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")
    assert browser.get("/api/v1/me").status_code == 401

    # Invited again, comes back with the same account and its password.
    second_browser = Client()
    assert accept(second_browser, invite(admin, ws_a), "existing") == 200
    mila.refresh_from_db()
    assert mila.is_active
    assert Membership.objects.filter(user=mila, workspace=ws_a).exists()
    assert second_browser.get("/api/v1/me").status_code == 200

    # What the removal ended stays ended.
    r = phone.get("/api/v1/workspaces", headers=bearer(old_token))
    assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")
    assert browser.get("/api/v1/me").status_code == 401

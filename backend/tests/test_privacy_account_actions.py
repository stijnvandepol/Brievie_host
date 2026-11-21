"""Account actions follow who reads which post (plan Task 28, PB T14, S 4.19, DECISIONS
"6. Reset links": once a member can have private letters, only the server admin makes a
reset link for that member)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import AccountNotice, PasswordReset, User
from apps.documents.models import DocumentReader
from apps.workspaces.context import SYSTEM
from tests.helpers import post_json
from tests.test_accounts_reset_api import complete, make_token, signed_in
from tests.visibility_support import as_actor, home, letter, office, private_letter


@pytest.fixture(autouse=True)
def _fast_hashing_and_public_url(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]


def _reset(client: Client, target: User) -> tuple[int, str]:
    r = post_json(client, f"/users/{target.id}/password-reset")
    return r.status_code, str(r.json().get("code", ""))


def test_workspace_admin_cannot_reset_a_reader_of_private_post() -> None:
    h = home()
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    assert _reset(signed_in(h.anne), h.bob) == (403, "server_admin_only")
    assert not PasswordReset.objects.filter(user=h.bob).exists()
    assert not AccountNotice.objects.filter(user=h.bob).exists()


def test_workspace_admin_cannot_end_sessions_or_revoke_devices_of_a_reader() -> None:
    h = home()
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    client = signed_in(h.anne)
    r = client.delete(f"/api/v1/users/{h.bob.id}/sessions")
    assert (r.status_code, r.json()["code"]) == (403, "server_admin_only")
    r = client.get(f"/api/v1/users/{h.bob.id}/devices")
    assert (r.status_code, r.json()["code"]) == (403, "server_admin_only")


def test_server_admin_may_act_and_the_member_gets_the_notice() -> None:
    h = home()
    User.objects.filter(id=h.anne.id).update(is_server_admin=True)
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    make_token(signed_in(h.anne), h.bob)
    assert AccountNotice.objects.filter(user=h.bob, code="password_reset_created").exists()


def test_workspace_admin_may_still_reset_a_member_without_private_post() -> None:
    h = home()
    letter(h.workspace, on=[h.persons["Bob"]])
    assert _reset(signed_in(h.anne), h.bob)[0] == 201


def test_reset_link_stops_when_the_member_became_a_reader() -> None:
    h = home()
    token = make_token(signed_in(h.anne), h.bob)
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    status, body = complete(Client(), token)
    assert status == 400
    assert body["code"] == "reset_link_invalid"


def test_emergency_rows_do_not_make_a_reader() -> None:
    h = home()
    doc = private_letter(h.workspace, addressees=[h.persons["Carla"]])
    with as_actor(h.workspace, SYSTEM):
        DocumentReader.objects.create(
            workspace=h.workspace,
            document=doc,
            user=h.bob,
            reason=DocumentReader.Reason.EMERGENCY,
            expires_at=timezone.now() + timedelta(hours=1),
        )
    assert _reset(signed_in(h.anne), h.bob)[0] == 201


def test_list_members_hides_account_actions_for_readers() -> None:
    h = home()
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    r = signed_in(h.anne).get(f"/api/v1/workspaces/{h.workspace.id}/members")
    can = {row["name"]: row["can_manage_account"] for row in r.json()}
    assert can == {"Anne": False, "Bob": False, "Carla": True}


def test_admin_who_does_not_read_all_post_cannot_reset_anybody() -> None:
    o = office()  # E off, A off
    assert _reset(signed_in(o.admin), o.plain) == (403, "server_admin_only")
    r = signed_in(o.admin).get(f"/api/v1/workspaces/{o.workspace.id}/members")
    assert not any(row["can_manage_account"] for row in r.json())


def test_admin_may_reset_when_admins_read_all() -> None:
    o = office()
    o.state(e=False, d=True, a=True)
    assert _reset(signed_in(o.admin), o.plain)[0] == 201
    # Femke reads a private letter: still the server admin's.
    assert _reset(signed_in(o.admin), o.finance) == (403, "server_admin_only")

"""An admin clears a lost second factor (two-step sign-in S 7, plan Task 9)."""

import io
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import AccountNotice, SecondFactor, User, UserSession
from apps.audit.models import ServerAuditEvent, WorkspaceAuditEvent
from apps.devices.models import Device
from apps.devices.services import create_pairing_code, pair_device
from apps.workspaces.context import SYSTEM, workspace_context
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json
from tests.second_factor_support import give_factor, password_step, signed_in_with_code
from tests.visibility_support import Home, home, private_letter

WRONG_PASSWORD = TEST_PASSWORD + "-fout"


@pytest.fixture(autouse=True)
def _fast_hashing(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def signed_in(user: User) -> Client:
    client = Client()
    assert password_step(client, user) == 200
    return client


def clear(
    client: Client, target: User, *, password: str = TEST_PASSWORD, **extra: object
) -> tuple[int, dict[str, object]]:
    body: dict[str, object] = {"password": password, **extra}
    r = post_json(client, f"/users/{target.id}/second-factor/clear", body)
    return r.status_code, r.json()


def paired(user: User) -> Device:
    _, code = create_pairing_code(user)
    device, _token = pair_device(code=code, device_name="Telefoon")
    return device


@pytest.fixture
def h() -> Home:
    return home()


def test_a_workspace_admin_clears_a_member_of_only_that_workspace(h: Home) -> None:
    give_factor(h.bob)
    paired(h.bob)
    UserSession.objects.create(
        user=h.bob, session_key="s" * 32, method="password", last_used_at=timezone.now()
    )

    status, body = clear(signed_in(h.anne), h.bob)

    assert (status, body) == (200, {"devices_revoked": 1})
    assert not SecondFactor.objects.filter(user=h.bob).exists()
    assert not UserSession.objects.filter(user=h.bob).exists()
    assert not Device.objects.filter(user=h.bob, revoked_at__isnull=True).exists()
    (notice,) = AccountNotice.objects.filter(user=h.bob, code="second_factor_cleared")
    assert notice.actor_name == "Anne"
    (row,) = ServerAuditEvent.objects.filter(code="second_factor_cleared")
    assert row.values == {"via": "admin", "devices_revoked": 1}
    assert (row.actor_id, row.target_id) == (h.anne.id, h.bob.id)
    with workspace_context(h.workspace.id, actor=SYSTEM):
        assert WorkspaceAuditEvent.objects.filter(code="second_factor_cleared").count() == 1


def test_devices_are_kept_when_asked(h: Home) -> None:
    give_factor(h.bob)
    paired(h.bob)

    status, body = clear(signed_in(h.anne), h.bob, revoke_devices=False)

    assert (status, body) == (200, {"devices_revoked": 0})
    assert Device.objects.filter(user=h.bob, revoked_at__isnull=True).count() == 1


def test_clear_follows_account_authority(h: Home) -> None:
    give_factor(h.bob)
    private_letter(h.workspace, addressees=[h.persons["Bob"]])

    status, body = clear(signed_in(h.anne), h.bob)

    assert (status, body["code"]) == (403, "server_admin_only")
    assert SecondFactor.objects.filter(user=h.bob).exists()
    assert not AccountNotice.objects.filter(user=h.bob).exists()


def test_a_server_admin_clears_anyone(h: Home) -> None:
    give_factor(h.bob)
    private_letter(h.workspace, addressees=[h.persons["Bob"]])
    admin = UserFactory.create(name="Beheer", is_server_admin=True)

    assert clear(signed_in(admin), h.bob)[0] == 200


def test_a_member_cannot_clear(h: Home) -> None:
    give_factor(h.anne)

    status, body = clear(signed_in(h.bob), h.anne)

    assert (status, body["code"]) == (404, "user_not_found")
    assert SecondFactor.objects.filter(user=h.anne).exists()


def test_own_account_is_refused(h: Home) -> None:
    factor = give_factor(h.anne)

    status, body = clear(signed_in_with_code(h.anne, factor), h.anne)

    assert (status, body["code"]) == (403, "own_account")
    assert SecondFactor.objects.filter(user=h.anne).exists()


def test_a_wrong_admin_password_is_400_and_counted(h: Home) -> None:
    give_factor(h.bob)
    client = signed_in(h.anne)

    statuses = [clear(client, h.bob, password=WRONG_PASSWORD)[0] for _ in range(6)]

    assert statuses == [400] * 5 + [429]
    assert SecondFactor.objects.filter(user=h.bob).exists()
    locked = ServerAuditEvent.objects.get(code="sign_in_locked_out")
    assert locked.values == {"via": "second_factor_clear"}


def test_an_admin_with_a_factor_needs_a_session_that_passed_it(h: Home) -> None:
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.http import HttpResponse
    from django.test import RequestFactory

    from apps.accounts.user_actions import clear_user_second_factor
    from apps.common.errors import ApiError

    give_factor(h.bob)
    anne_factor = give_factor(h.anne)
    # A session without the second step (the middleware signs such a session out; the
    # service refuses it on its own as well).
    request = RequestFactory().post("/api/v1/users/x/second-factor/clear")
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request.session.save()
    request.user = h.anne
    key = request.session.session_key
    assert key is not None
    UserSession.objects.create(
        user=h.anne, session_key=key, method="password", last_used_at=timezone.now()
    )

    with pytest.raises(ApiError) as caught:
        clear_user_second_factor(
            request, target_id=h.bob.id, password=TEST_PASSWORD, revoke_devices=True
        )

    assert (caught.value.status, caught.value.code) == (403, "second_factor_session_required")
    assert caught.value.message == "Log opnieuw in met je code om dit te doen."
    assert clear(signed_in_with_code(h.anne, anne_factor), h.bob)[0] == 200


def test_a_member_without_a_factor_is_404(h: Home) -> None:
    status, body = clear(signed_in(h.anne), h.bob)

    assert (status, body["code"]) == (404, "second_factor_not_set")


def test_after_clearing_a_required_member_goes_straight_to_setting_up(
    settings: Settings, h: Home
) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    User.objects.filter(pk=h.bob.pk).update(
        second_factor_required_since=timezone.now() - timedelta(days=2)
    )
    settings.BRIEVIE_SECOND_FACTOR_GRACE_DAYS = 1
    give_factor(h.bob)
    admin = UserFactory.create(is_server_admin=True)
    admin_factor = give_factor(admin)

    assert clear(signed_in_with_code(admin, admin_factor), h.bob)[0] == 200

    r = post_json(Client(), "/auth/login", {"email": h.bob.email, "password": TEST_PASSWORD})
    assert (r.status_code, r.json()["step"]) == (202, "setup")


def test_a_device_token_cannot_clear(h: Home) -> None:
    give_factor(h.bob)
    _, code = create_pairing_code(h.anne)
    _, token = pair_device(code=code, device_name="iPhone")

    r = post_json(
        Client(),
        f"/users/{h.bob.id}/second-factor/clear",
        {"password": TEST_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 401


# --- The command ------------------------------------------------------------------------


def test_the_command_clears_and_logs_the_system() -> None:
    user = UserFactory.create(email="beheer@example.nl", is_server_admin=True)
    give_factor(user)
    paired(user)
    out = io.StringIO()

    call_command("clear_second_factor", "--email", "Beheer@Example.nl", stdout=out)

    assert not SecondFactor.objects.filter(user=user).exists()
    assert "1 apparaten zijn ontkoppeld" in out.getvalue()
    assert "beheer@example.nl" not in out.getvalue()
    (row,) = ServerAuditEvent.objects.filter(code="second_factor_cleared")
    assert (row.actor_kind, row.values) == ("system", {"via": "command", "devices_revoked": 1})
    # Pairing left a notice too; clearing adds its own.
    assert AccountNotice.objects.filter(user=user, code="second_factor_cleared_by_command").exists()


def test_the_command_keeps_devices_when_asked() -> None:
    user = UserFactory.create(email="beheer@example.nl")
    give_factor(user)
    paired(user)

    call_command(
        "clear_second_factor", "--email", user.email, "--keep-devices", stdout=io.StringIO()
    )

    assert Device.objects.filter(user=user, revoked_at__isnull=True).count() == 1


def test_the_command_refuses_an_unknown_address() -> None:
    with pytest.raises(CommandError, match="geen account"):
        call_command("clear_second_factor", "--email", "niemand@example.nl")


def test_the_command_says_when_there_is_nothing_to_clear() -> None:
    user = UserFactory.create()

    with pytest.raises(CommandError, match="geen tweestapsverificatie"):
        call_command("clear_second_factor", "--email", user.email)

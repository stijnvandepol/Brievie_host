"""The recovery sheet from the web interface: session only, server admins only, after a
recent sign-in, never cached, audited, and the key never in a log."""

import logging
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User, UserSession
from apps.audit.models import ServerAuditEvent
from apps.backup.api import SHEET_FILE_NAME
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

URL = "/server/recovery-sheet"
KEY_GROUPS = " ".join(["5a5a"] * 16)  # the suite's master key, 5a repeated, in groups of four


@pytest.fixture(autouse=True)
def compose_install(settings: Settings) -> None:
    settings.BRIEVIE_COMPOSE_INSTALL = True
    settings.BRIEVIE_HOSTNAME = "mijnnas.local"


def sign_in(user: User) -> Client:
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return client


def admin() -> User:
    return UserFactory.create(name="Beheer", is_server_admin=True)


def test_a_server_admin_gets_the_sheet_with_the_key_and_safe_headers() -> None:
    user = admin()
    response = post_json(sign_in(user), URL)
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert response["Cache-Control"] == "no-store"
    assert SHEET_FILE_NAME in response["Content-Disposition"]
    assert response["Content-Disposition"].startswith("attachment;")
    body = response.content.decode()
    assert KEY_GROUPS in body
    assert "mijnnas.local" in body


def test_one_audit_row_names_the_admin_and_holds_no_key() -> None:
    user = admin()
    post_json(sign_in(user), URL)
    rows = list(ServerAuditEvent.objects.filter(code="recovery_sheet_viewed"))
    assert len(rows) == 1
    assert rows[0].actor_id == user.id
    assert rows[0].values == {}
    assert "5a5a" not in str(rows[0].__dict__)


def test_no_session_is_401() -> None:
    assert post_json(Client(), URL).status_code == 401


def test_a_device_token_is_401(settings: Settings) -> None:
    user = admin()
    code = post_json(sign_in(user), "/devices/pairing-codes").json()["code"]
    token = post_json(Client(), "/devices/pair", {"code": code, "device_name": "A"}).json()["token"]
    r = post_json(Client(), URL, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert not ServerAuditEvent.objects.filter(code="recovery_sheet_viewed").exists()


def test_a_member_who_is_no_server_admin_is_403() -> None:
    user = UserFactory.create(name="Lid")
    r = post_json(sign_in(user), URL)
    assert (r.status_code, r.json()["code"]) == (403, "forbidden")
    assert b"5a5a" not in r.content
    assert not ServerAuditEvent.objects.filter(code="recovery_sheet_viewed").exists()


def test_an_old_session_must_type_the_password_again() -> None:
    user = admin()
    client = sign_in(user)
    UserSession.objects.filter(user=user).update(created_at=timezone.now() - timedelta(minutes=16))
    r = post_json(client, URL)
    assert (r.status_code, r.json()["code"]) == (403, "reauthentication_required")
    assert b"5a5a" not in r.content
    assert not ServerAuditEvent.objects.filter(code="recovery_sheet_viewed").exists()
    post_json(client, "/auth/reauthenticate", {"password": TEST_PASSWORD})
    assert post_json(client, URL).status_code == 200


def test_a_wrapper_server_does_not_make_the_sheet_here(settings: Settings) -> None:
    settings.BRIEVIE_COMPOSE_INSTALL = False
    r = post_json(sign_in(admin()), URL)
    assert (r.status_code, r.json()["code"]) == (409, "recovery_sheet_not_here")
    assert b"5a5a" not in r.content


def test_a_get_is_not_allowed() -> None:
    r = sign_in(admin()).get(f"/api/v1{URL}")
    assert r.status_code == 405
    assert b"5a5a" not in r.content


def test_the_key_reaches_no_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        post_json(sign_in(admin()), URL)
    assert "5a5a" not in caplog.text
    assert "5a" * 32 not in caplog.text


def test_the_sheet_uses_the_api_container_environment(settings: Settings) -> None:
    """The api has the master key, the host name and (compose) a back-up folder, but no
    superuser; the sheet needs nothing more."""
    settings.BRIEVIE_BACKUP_DIR_SET = True
    settings.BRIEVIE_BACKUP_DIR_HOST = "/volume1/brievie-backup"
    settings.BRIEVIE_BACKUP_PASSWORD = "a-separate-backup-password"
    body = post_json(sign_in(admin()), URL).content.decode()
    assert "/volume1/brievie-backup" in body
    assert "a-separate-backup-password" in body


def test_the_sheet_is_rate_limited_per_admin() -> None:
    client = sign_in(admin())
    for _ in range(6):
        assert post_json(client, URL).status_code == 200
    r = post_json(client, URL)
    assert (r.status_code, r.json()["code"]) == (429, "too_many_attempts")
    assert b"5a5a" not in r.content


def test_the_sheet_needs_a_csrf_token_from_a_browser_session() -> None:
    user = admin()
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    r = client.post(f"/api/v1{URL}", content_type="application/json")
    assert r.status_code == 403
    assert r.json()["code"] == "csrf_failed"
    assert b"5a5a" not in r.content
    assert not ServerAuditEvent.objects.filter(code="recovery_sheet_viewed").exists()


# --- With two-step verification (security review of PR #52, point 6) ------------------


def test_with_a_factor_a_session_that_passed_the_code_gets_the_sheet() -> None:
    from tests.second_factor_support import give_factor, signed_in_with_code

    user = admin()
    factor = give_factor(user)

    assert post_json(signed_in_with_code(user, factor), URL).status_code == 200


def test_with_a_factor_a_session_without_the_code_is_refused() -> None:
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.http import HttpResponse
    from django.test import RequestFactory

    from apps.backup.api import create_recovery_sheet
    from apps.common.errors import ApiError
    from tests.second_factor_support import give_factor

    user = admin()
    give_factor(user)
    # The middleware signs such a session out; the endpoint refuses it on its own too.
    request = RequestFactory().post("/api/v1" + URL)
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request.session.save()
    request.user = user
    key = request.session.session_key
    assert key is not None
    UserSession.objects.create(
        user=user, session_key=key, method="password", last_used_at=timezone.now()
    )

    with pytest.raises(ApiError) as caught:
        create_recovery_sheet(request)

    assert (caught.value.status, caught.value.code) == (403, "second_factor_session_required")
    assert not ServerAuditEvent.objects.filter(code="recovery_sheet_viewed").exists()

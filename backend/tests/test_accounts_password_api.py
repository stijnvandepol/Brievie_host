import logging

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import User, UserSession
from apps.audit.models import ServerAuditEvent
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

NEW_PASSWORD = "een-nieuw-lang-wachtwoord-2026"


@pytest.fixture(autouse=True)
def _fast_hashing(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def signed_in(user: User) -> Client:
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return client


def change(client: Client, current: str = TEST_PASSWORD, new: str = NEW_PASSWORD) -> int:
    body = {"current_password": current, "new_password": new}
    return post_json(client, "/me/password", body).status_code


def fresh(user: User) -> User:
    return User.objects.get(pk=user.pk)


def test_change_password_ends_the_other_sessions_and_keeps_this_one() -> None:
    user = UserFactory.create()
    mine = signed_in(user)
    other = signed_in(user)
    old_key = mine.session.session_key

    assert change(mine) == 204

    assert fresh(user).check_password(NEW_PASSWORD)
    assert mine.get("/api/v1/me").status_code == 200
    new_key = mine.session.session_key
    assert new_key != old_key
    assert UserSession.objects.get(user=user).session_key == new_key
    assert other.get("/api/v1/me").status_code == 401


def test_change_password_with_a_wrong_current_password_is_400() -> None:
    user = UserFactory.create()
    mine = signed_in(user)
    other = signed_in(user)

    r = post_json(
        mine, "/me/password", {"current_password": "fout-wachtwoord", "new_password": NEW_PASSWORD}
    )

    assert r.status_code == 400
    assert r.json()["code"] == "wrong_password"
    assert fresh(user).check_password(TEST_PASSWORD)
    assert other.get("/api/v1/me").status_code == 200
    assert not ServerAuditEvent.objects.filter(code="password_changed").exists()


def test_change_password_runs_the_password_rules() -> None:
    user = UserFactory.create()
    mine = signed_in(user)

    assert change(mine, new="kort") == 422
    assert fresh(user).check_password(TEST_PASSWORD)


def test_change_password_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    user = UserFactory.create()
    mine = signed_in(user)
    signed_in(user)

    with caplog.at_level(logging.DEBUG):
        assert change(mine) == 204

    (changed,) = ServerAuditEvent.objects.filter(code="password_changed")
    assert (changed.actor_id, changed.values) == (user.id, {"via": "self"})
    (ended,) = ServerAuditEvent.objects.filter(code="session_ended")
    assert ended.values == {"count": 1, "reason": "password_changed"}
    assert NEW_PASSWORD not in caplog.text
    assert TEST_PASSWORD not in caplog.text


def test_change_password_without_other_sessions_logs_no_ending() -> None:
    mine = signed_in(UserFactory.create())

    assert change(mine) == 204

    assert not ServerAuditEvent.objects.filter(code="session_ended").exists()


def test_wrong_current_passwords_lock_like_sign_in() -> None:
    user = UserFactory.create()
    mine = signed_in(user)

    statuses = [change(mine, current="fout-wachtwoord") for _ in range(5)]
    r = post_json(
        mine, "/me/password", {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert statuses == [400] * 5
    assert r.status_code == 429
    assert r.json()["code"] == "too_many_attempts"
    assert fresh(user).check_password(TEST_PASSWORD)
    (locked,) = ServerAuditEvent.objects.filter(code="sign_in_locked_out")
    assert (locked.target_id, locked.values) == (user.id, {"via": "password_change"})
    # The same lock as signing in: email and address.
    login = {"email": user.email, "password": TEST_PASSWORD}
    assert post_json(Client(), "/auth/login", login).status_code == 429


def test_change_password_is_throttled_per_user() -> None:
    user = UserFactory.create()
    mine = signed_in(user)

    statuses = [change(mine, new="kort") for _ in range(11)]

    assert statuses[:10] == [422] * 10
    assert statuses[10] == 429
    assert change(signed_in(UserFactory.create()), new="kort") == 422


def test_change_password_needs_a_session() -> None:
    assert change(Client()) == 401

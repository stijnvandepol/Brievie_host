"""Signing in with a code after the password (two-step sign-in, plan Task 7)."""

import threading

import pytest
from django.db import connection
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import SecondFactor, User, UserSession
from apps.accounts.signin_limits import KNOWN_BROWSER_COOKIE
from apps.audit.models import ServerAuditEvent
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json
from tests.second_factor_support import Factor, give_factor, password_step

NEW_PASSWORD = TEST_PASSWORD + "-nieuw"


@pytest.fixture(autouse=True)
def _fast_hashing(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def user() -> User:
    return UserFactory.create()


@pytest.fixture
def factor(user: User) -> Factor:
    return give_factor(user)


def send_code(client: Client, **body: str) -> tuple[int, dict[str, object]]:
    r = post_json(client, "/auth/login/code", body)
    return r.status_code, r.json()


def at_the_code_step(user: User) -> Client:
    client = Client()
    assert password_step(client, user) == 202
    return client


def rows(code: str) -> list[ServerAuditEvent]:
    return list(ServerAuditEvent.objects.filter(code=code).order_by("created_at"))


def test_the_right_code_signs_in_with_the_second_step(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)

    status, body = send_code(client, code=factor.code())

    assert status == 200
    assert body["id"] == str(user.id)
    assert body["has_second_factor"] is True
    session = UserSession.objects.get(user=user)
    assert (session.second_factor, session.created_via) == (True, "password")
    assert client.get("/api/v1/me").status_code == 200
    (signed,) = rows("sign_in_succeeded")
    assert signed.values == {"via": "password", "second_factor": "totp"}
    (listed,) = client.get("/api/v1/me/sessions").json()
    assert listed["second_factor"] is True


def test_known_browser_cookie_only_after_the_code(user: User, factor: Factor) -> None:
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})

    assert r.status_code == 202
    assert KNOWN_BROWSER_COOKIE not in r.cookies

    r = post_json(client, "/auth/login/code", {"code": factor.code()})

    assert r.status_code == 200
    assert KNOWN_BROWSER_COOKIE in r.cookies


def test_a_wrong_code_is_401_and_logged(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)

    status, body = send_code(client, code="000000")

    assert (status, body) == (
        401,
        {"code": "second_factor_invalid", "message": "Code klopt niet. Probeer het opnieuw."},
    )
    (failed,) = rows("sign_in_failed")
    assert failed.values == {"step": "second_factor"}
    assert client.get("/api/v1/me").status_code == 401
    # The pending step is still there: the right code now signs in.
    assert send_code(client, code=factor.code())[0] == 200


def test_five_wrong_codes_end_the_pending_sign_in(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)
    answers = [send_code(client, code="000000")[1]["code"] for _ in range(5)]

    status, body = send_code(client, code=factor.code())

    assert answers == ["second_factor_invalid"] * 4 + ["second_factor_locked"]
    assert (status, body["code"]) == (401, "sign_in_expired")
    assert body["message"] == "Je bent te lang bezig geweest. Log opnieuw in."


def test_the_lock_holds_for_a_new_sign_in_with_the_right_code(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)
    for _ in range(5):
        send_code(client, code="000000")
    other = at_the_code_step(user)

    r = post_json(other, "/auth/login/code", {"code": factor.code()})

    assert (r.status_code, r.json()["code"]) == (429, "second_factor_locked")
    assert r.json()["message"] == (
        "Te vaak een verkeerde code. Probeer het over 15 minuten opnieuw."
    )
    assert 1 <= int(r["Retry-After"]) <= 15 * 60 + 1
    assert not UserSession.objects.filter(user=user).exists()


def test_a_recovery_code_signs_in(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)

    status, _body = send_code(client, recovery_code=factor.recovery_codes[0].lower())

    assert status == 200
    assert UserSession.objects.get(user=user).second_factor is True
    assert rows("sign_in_succeeded")[0].values["second_factor"] == "recovery_code"
    notices = client.get("/api/v1/me/notices").json()
    assert [n["code"] for n in notices] == ["second_factor_recovery_used"]


def test_a_used_recovery_code_is_refused(user: User, factor: Factor) -> None:
    send_code(at_the_code_step(user), recovery_code=factor.recovery_codes[0])

    status, body = send_code(at_the_code_step(user), recovery_code=factor.recovery_codes[0])

    assert (status, body["code"]) == (401, "second_factor_invalid")
    assert rows("sign_in_failed")[0].values == {"step": "recovery_code"}


@pytest.mark.parametrize(
    "body", [{}, {"code": "", "recovery_code": ""}, {"code": "123456", "recovery_code": "A"}]
)
def test_exactly_one_of_code_and_recovery_code(
    user: User, factor: Factor, body: dict[str, str]
) -> None:
    status, answer = send_code(at_the_code_step(user), **body)

    assert (status, answer["code"]) == (422, "validation_error")


def test_without_a_pending_sign_in_the_code_is_expired(user: User, factor: Factor) -> None:
    status, body = send_code(Client(), code=factor.code())

    assert (status, body["code"]) == (401, "sign_in_expired")
    assert not UserSession.objects.exists()


def test_a_signed_in_session_has_no_code_step(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)
    send_code(client, code=factor.code())

    status, body = send_code(client, code=factor.code(offset=1))

    assert (status, body["code"]) == (401, "sign_in_expired")


def test_a_code_step_ends_when_the_factor_is_cleared(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)
    SecondFactor.objects.filter(user=user).delete()
    give_factor(user)

    status, body = send_code(client, code=factor.code())

    assert (status, body["code"]) == (401, "sign_in_expired")


def test_a_code_step_ends_when_the_password_changes(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)
    user.set_password(NEW_PASSWORD)
    user.save()

    status, body = send_code(client, code=factor.code())

    assert (status, body["code"]) == (401, "sign_in_expired")


def test_the_answer_holds_no_code_or_secret(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)

    r = post_json(client, "/auth/login/code", {"code": factor.code()})

    assert set(r.json()) == {
        "id",
        "email",
        "name",
        "language",
        "explanation_language",
        "is_server_admin",
        "has_second_factor",
    }


def test_the_code_step_is_throttled_per_address(user: User, factor: Factor) -> None:
    client = at_the_code_step(user)
    answers = [send_code(client, code="000000")[0] for _ in range(11)]

    assert answers[-1] == 429


@pytest.mark.django_db(transaction=True)
def test_two_parallel_sign_ins_with_one_code_one_wins(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
    user = UserFactory.create()
    factor = give_factor(user)
    browsers = [at_the_code_step(user), at_the_code_step(user)]
    code = factor.code()
    barrier = threading.Barrier(2)
    statuses: list[int] = []

    def run(client: Client) -> None:
        try:
            barrier.wait(timeout=10)
            statuses.append(post_json(client, "/auth/login/code", {"code": code}).status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(client,)) for client in browsers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(statuses) == [200, 401]
    assert UserSession.objects.filter(user=user).count() == 1

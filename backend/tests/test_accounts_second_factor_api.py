"""Your own two-step verification through the API ("Beveiliging", plan Task 8)."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts import totp
from apps.accounts.models import RecoveryCode, SecondFactor, SignInFailures, User, UserSession
from apps.devices.services import create_pairing_code, pair_device
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json
from tests.second_factor_support import Factor, give_factor, password_step, secret_of

WRONG_PASSWORD = TEST_PASSWORD + "-fout"


@pytest.fixture(autouse=True)
def _fast_hashing(settings: Settings) -> None:
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture
def user() -> User:
    return UserFactory.create()


def signed_in(user: User) -> Client:
    client = Client()
    assert password_step(client, user) == 200
    return client


def bearer(user: User) -> dict[str, str]:
    _, code = create_pairing_code(user)
    _, token = pair_device(code=code, device_name="iPhone")
    return {"Authorization": f"Bearer {token}"}


def set_up(client: Client) -> tuple[bytes, list[str]]:
    r = post_json(client, "/me/second-factor/setup")
    assert r.status_code == 200, r.json()
    secret = secret_of(r.json()["secret"])
    code = totp.code_at(secret, totp.step_of(timezone.now()))
    confirm = post_json(client, "/me/second-factor/setup/confirm", {"code": code})
    assert confirm.status_code == 200, confirm.json()
    codes: list[str] = confirm.json()["recovery_codes"]
    return secret, codes


def delete_json(client: Client, path: str, body: dict[str, str], **extra: str) -> int:
    return client.delete(
        f"/api/v1{path}", body, content_type="application/json", headers=extra or None
    ).status_code


OPERATIONS = [
    ("get", "/me/second-factor", None),
    ("post", "/me/second-factor/setup", None),
    ("post", "/me/second-factor/setup/confirm", {"code": "123456"}),
    ("post", "/me/second-factor/recovery-codes", {"code": "123456"}),
    ("delete", "/me/second-factor", {"password": WRONG_PASSWORD, "code": "123456"}),
]


def call(client: Client, method: str, path: str, body: object, **headers: str) -> int:
    url = f"/api/v1{path}"
    if method == "get":
        return client.get(url, headers=headers or None).status_code
    if method == "delete":
        return client.delete(
            url, body, content_type="application/json", headers=headers or None
        ).status_code
    if body is None:
        return client.post(
            url, content_type="application/json", headers=headers or None
        ).status_code
    return client.post(
        url, body, content_type="application/json", headers=headers or None
    ).status_code


@pytest.mark.parametrize(("method", "path", "body"), OPERATIONS)
def test_a_device_token_gets_401(user: User, method: str, path: str, body: object) -> None:
    assert call(Client(), method, path, body, **bearer(user)) == 401


@pytest.mark.parametrize(("method", "path", "body"), OPERATIONS)
def test_nobody_gets_401(method: str, path: str, body: object) -> None:
    assert call(Client(), method, path, body) == 401


def test_status_without_a_factor(user: User) -> None:
    body = signed_in(user).get("/api/v1/me/second-factor").json()

    assert body == {
        "enabled": False,
        "required": False,
        "required_by": "none",
        "required_by_workspaces": [],
        "grace_until": None,
        "recovery_codes_left": 0,
    }


def test_status_in_grace(settings: Settings, user: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    client = signed_in(user)

    body = client.get("/api/v1/me/second-factor").json()

    assert (body["required"], body["required_by"]) == (True, "server")
    assert body["grace_until"] is not None


def test_set_up_and_confirm_through_a_session(user: User) -> None:
    client = signed_in(user)

    r = post_json(client, "/me/second-factor/setup")

    assert r.status_code == 200
    assert r["Cache-Control"] == "no-store"
    assert set(r.json()) == {"secret", "otpauth_uri"}
    assert r.json()["otpauth_uri"].startswith("otpauth://totp/Brievie%20%28testserver%29:")
    secret = secret_of(r.json()["secret"])
    code = totp.code_at(secret, totp.step_of(timezone.now()))
    confirm = post_json(client, "/me/second-factor/setup/confirm", {"code": code})
    assert confirm.status_code == 200
    assert confirm["Cache-Control"] == "no-store"
    assert len(confirm.json()["recovery_codes"]) == 10
    status = client.get("/api/v1/me/second-factor").json()
    assert (status["enabled"], status["recovery_codes_left"]) == (True, 10)
    assert UserSession.objects.get(user=user).second_factor is True


def test_confirm_with_a_session_ends_the_other_sessions(user: User) -> None:
    other = signed_in(user)
    client = signed_in(user)

    set_up(client)

    assert other.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me").status_code == 200
    assert UserSession.objects.filter(user=user).count() == 1


def test_an_old_session_must_type_the_password_before_setting_up(user: User) -> None:
    client = signed_in(user)
    UserSession.objects.filter(user=user).update(created_at=timezone.now() - timedelta(minutes=16))

    r = post_json(client, "/me/second-factor/setup")

    assert (r.status_code, r.json()["code"]) == (403, "reauthentication_required")
    assert not SecondFactor.objects.exists()


def test_the_secret_is_shown_only_when_setting_up(user: User) -> None:
    client = signed_in(user)
    first = post_json(client, "/me/second-factor/setup").json()["secret"]

    status = client.get("/api/v1/me/second-factor")

    assert first.replace(" ", "") not in status.content.decode()
    assert "otpauth" not in status.content.decode()


def test_setting_up_twice_is_refused(user: User) -> None:
    client = signed_in(user)
    set_up(client)

    r = post_json(client, "/me/second-factor/setup")

    assert (r.status_code, r.json()["code"]) == (409, "second_factor_exists")


def test_a_wrong_confirm_code_is_400(user: User) -> None:
    client = signed_in(user)
    post_json(client, "/me/second-factor/setup")

    r = post_json(client, "/me/second-factor/setup/confirm", {"code": "000000"})

    assert (r.status_code, r.json()["code"]) == (400, "second_factor_invalid")
    assert client.get("/api/v1/me").status_code == 200


# --- The pending step `setup` ---------------------------------------------------------


@pytest.fixture
def must_set_up(settings: Settings, user: User) -> Client:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    User.objects.filter(pk=user.pk).update(
        second_factor_required_since=timezone.now() - timedelta(days=30)
    )
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert (r.status_code, r.json()["step"]) == (202, "setup")
    return client


def test_the_pending_setup_reaches_only_the_set_up_calls(must_set_up: Client) -> None:
    assert must_set_up.get("/api/v1/me/second-factor").status_code == 200
    assert must_set_up.get("/api/v1/me").status_code == 401
    assert must_set_up.get("/api/v1/me/notices").status_code == 401
    assert (
        post_json(must_set_up, "/me/second-factor/recovery-codes", {"code": "1"}).status_code == 401
    )
    assert post_json(must_set_up, "/devices/pairing-codes").status_code == 401
    assert post_json(must_set_up, "/auth/login/code", {"code": "123456"}).status_code == 401


def test_confirming_the_pending_setup_signs_in(must_set_up: Client, user: User) -> None:
    secret, codes = set_up(must_set_up)

    assert len(codes) == 10
    assert must_set_up.get("/api/v1/me").status_code == 200
    session = UserSession.objects.get(user=user)
    assert (session.second_factor, session.created_via) == (True, "password")
    assert SecondFactor.objects.get(user=user).confirmed_at is not None
    assert secret


def test_a_code_step_does_not_reach_the_set_up(user: User) -> None:
    give_factor(user)
    client = Client()
    assert password_step(client, user) == 202

    assert post_json(client, "/me/second-factor/setup").status_code == 401


# --- Renewing and removing ------------------------------------------------------------


@pytest.fixture
def with_factor(user: User) -> tuple[Client, Factor]:
    factor = give_factor(user)
    client = Client()
    assert password_step(client, user) == 202
    assert post_json(client, "/auth/login/code", {"code": factor.code()}).status_code == 200
    return client, factor


def test_renewing_recovery_codes(user: User, with_factor: tuple[Client, Factor]) -> None:
    client, factor = with_factor

    r = post_json(client, "/me/second-factor/recovery-codes", {"code": factor.code(offset=1)})

    assert r.status_code == 200
    assert r["Cache-Control"] == "no-store"
    assert set(r.json()["recovery_codes"]).isdisjoint(factor.recovery_codes)
    assert RecoveryCode.objects.filter(user=user, used_at__isnull=True).count() == 10


def test_renewing_with_a_wrong_code_is_400(with_factor: tuple[Client, Factor]) -> None:
    client, _factor = with_factor

    r = post_json(client, "/me/second-factor/recovery-codes", {"code": "000000"})

    assert (r.status_code, r.json()["code"]) == (400, "second_factor_invalid")
    assert client.get("/api/v1/me").status_code == 200


def test_removing_needs_password_and_code(user: User, with_factor: tuple[Client, Factor]) -> None:
    client, factor = with_factor

    status = delete_json(
        client, "/me/second-factor", {"password": TEST_PASSWORD, "code": factor.code(offset=1)}
    )

    assert status == 204
    assert not SecondFactor.objects.filter(user=user).exists()
    assert client.get("/api/v1/me").json()["has_second_factor"] is False


def test_removing_with_a_wrong_password_counts_for_axes(
    user: User, with_factor: tuple[Client, Factor]
) -> None:
    client, factor = with_factor
    statuses = [
        delete_json(
            client, "/me/second-factor", {"password": WRONG_PASSWORD, "code": factor.code()}
        )
        for _ in range(6)
    ]

    assert statuses == [400] * 5 + [429]
    assert SignInFailures.objects.get().failures == 5
    assert SecondFactor.objects.filter(user=user).exists()


def test_removing_while_required_is_409(
    settings: Settings, user: User, with_factor: tuple[Client, Factor]
) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    client, factor = with_factor

    r = client.delete(
        "/api/v1/me/second-factor",
        {"password": TEST_PASSWORD, "code": factor.code(offset=1)},
        content_type="application/json",
    )

    assert (r.status_code, r.json()["code"]) == (409, "second_factor_required")


# --- A new phone (plan Task 16) ---------------------------------------------------------


def test_moving_to_a_new_phone(user: User, with_factor: tuple[Client, Factor]) -> None:
    client, factor = with_factor
    other = Client()
    assert password_step(other, user) == 202
    assert post_json(other, "/auth/login/code", {"code": factor.code(offset=1)}).status_code == 200

    # The newest code is used up by the other sign-in: the old phone's proof is a recovery code.
    begun = post_json(
        client, "/me/second-factor/replacement", {"recovery_code": factor.recovery_codes[0]}
    )

    assert begun.status_code == 200, begun.json()
    assert begun["Cache-Control"] == "no-store"
    new_secret = secret_of(begun.json()["secret"])
    code = totp.code_at(new_secret, totp.step_of(timezone.now()))
    done = post_json(client, "/me/second-factor/replacement/confirm", {"code": code})
    assert done.status_code == 200
    assert done["Cache-Control"] == "no-store"
    assert len(done.json()["recovery_codes"]) == 10
    assert other.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me").status_code == 200


def test_a_new_phone_asks_exactly_one_proof(with_factor: tuple[Client, Factor]) -> None:
    client, _factor = with_factor

    r = post_json(client, "/me/second-factor/replacement", {"code": "", "recovery_code": ""})

    assert (r.status_code, r.json()["code"]) == (422, "validation_error")


@pytest.mark.parametrize(
    "path", ["/me/second-factor/replacement", "/me/second-factor/replacement/confirm"]
)
def test_a_new_phone_needs_a_web_session(user: User, path: str) -> None:
    r = post_json(Client(), path, {"code": "123456"}, headers=bearer(user))

    assert r.status_code == 401

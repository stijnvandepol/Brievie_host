"""Security review 2026-11-20 (identity M2): a browser left open must not quietly become a
phone with access. A pairing code needs a recent sign-in, and every paired device leaves
the owner a notice."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import AccountNotice, User, UserSession
from apps.devices.models import PairingCode
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json
from tests.second_factor_support import give_factor, signed_in_with_code


@pytest.fixture
def user() -> User:
    return UserFactory.create(name="Kees")


def signed_in(user: User) -> Client:
    client = Client()
    r = post_json(client, "/auth/login", {"email": user.email, "password": TEST_PASSWORD})
    assert r.status_code == 200
    return client


def signed_in_long_ago(user: User) -> Client:
    client = signed_in(user)
    UserSession.objects.filter(user=user).update(created_at=timezone.now() - timedelta(minutes=16))
    return client


def test_a_fresh_session_makes_a_code(user: User) -> None:
    assert post_json(signed_in(user), "/devices/pairing-codes").status_code == 201


def test_an_old_session_must_type_the_password_again(user: User) -> None:
    client = signed_in_long_ago(user)

    r = post_json(client, "/devices/pairing-codes")

    assert r.status_code == 403
    assert r.json() == {
        "code": "reauthentication_required",
        "message": "Vul eerst je wachtwoord opnieuw in.",
    }
    assert not PairingCode.objects.exists()


def test_after_typing_the_password_again_a_code_is_made(user: User) -> None:
    client = signed_in_long_ago(user)

    r = post_json(client, "/auth/reauthenticate", {"password": TEST_PASSWORD})

    assert r.status_code == 204
    assert post_json(client, "/devices/pairing-codes").status_code == 201


def test_a_wrong_password_does_not_count_as_recent(user: User) -> None:
    client = signed_in_long_ago(user)

    r = post_json(client, "/auth/reauthenticate", {"password": "fout-wachtwoord"})

    assert (r.status_code, r.json()["code"]) == (400, "wrong_password")
    assert post_json(client, "/devices/pairing-codes").status_code == 403


def test_wrong_passwords_lock_like_signing_in(user: User) -> None:
    client = signed_in_long_ago(user)
    for _ in range(5):
        post_json(client, "/auth/reauthenticate", {"password": "fout-wachtwoord"})

    r = post_json(client, "/auth/reauthenticate", {"password": TEST_PASSWORD})

    assert (r.status_code, r.json()["code"]) == (429, "too_many_attempts")


def test_reauthenticate_needs_a_session_not_a_device_token(user: User) -> None:
    code = post_json(signed_in(user), "/devices/pairing-codes").json()["code"]
    token = post_json(Client(), "/devices/pair", {"code": code, "device_name": "A"}).json()["token"]

    r = post_json(
        Client(),
        "/auth/reauthenticate",
        {"password": TEST_PASSWORD},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert (r.status_code, r.json()["code"]) == (401, "not_authenticated")


def test_pairing_leaves_the_owner_a_notice(user: User) -> None:
    browser = signed_in(user)
    code = post_json(browser, "/devices/pairing-codes").json()["code"]

    r = post_json(Client(), "/devices/pair", {"code": code, "device_name": "Telefoon van X"})

    assert r.status_code == 201
    (notice,) = AccountNotice.objects.filter(user=user)
    assert notice.code == "device_paired"
    assert notice.values == {"device": "Telefoon van X"}
    (shown,) = browser.get("/api/v1/me/notices").json()
    assert shown["code"] == "device_paired"
    assert "\u2018Telefoon van X\u2019 aan je account gekoppeld" in shown["message"]
    assert "Trek het in bij Apparaten" in shown["message"]


# --- With two-step verification (plan Task 10) -------------------------------------------


def test_a_session_that_passed_the_code_pairs_as_today(user: User) -> None:
    factor = give_factor(user)
    browser = signed_in_with_code(user, factor)

    code = post_json(browser, "/devices/pairing-codes")

    assert code.status_code == 201
    r = post_json(Client(), "/devices/pair", {"code": code.json()["code"], "device_name": "A"})
    assert r.status_code == 201
    assert AccountNotice.objects.filter(user=user, code="device_paired").exists()


def test_a_session_without_the_code_cannot_pair(user: User) -> None:
    give_factor(user)
    browser = Client()
    browser.force_login(user)  # a session that did not pass the second step

    r = post_json(browser, "/devices/pairing-codes")

    assert (r.status_code, r.json()["code"]) == (403, "second_factor_session_required")
    assert not PairingCode.objects.exists()


def test_reauthenticate_needs_the_code_when_it_is_on(user: User) -> None:
    factor = give_factor(user)
    browser = signed_in_with_code(user, factor)

    r = post_json(browser, "/auth/reauthenticate", {"password": TEST_PASSWORD})

    assert (r.status_code, r.json()["code"]) == (400, "second_factor_invalid")
    r = post_json(
        browser,
        "/auth/reauthenticate",
        {"password": TEST_PASSWORD, "code": factor.code(offset=1)},
    )
    assert r.status_code == 204


def test_reauthenticate_with_the_code_lets_an_old_session_pair(user: User) -> None:
    factor = give_factor(user)
    browser = Client()
    browser.force_login(user)
    UserSession.objects.filter(user=user).update(created_at=timezone.now() - timedelta(minutes=16))

    r = post_json(
        browser, "/auth/reauthenticate", {"password": TEST_PASSWORD, "code": factor.code()}
    )

    assert r.status_code == 204
    assert UserSession.objects.get(user=user).second_factor is True
    assert post_json(browser, "/devices/pairing-codes").status_code == 201


def test_reauthenticate_without_a_factor_needs_no_code(user: User) -> None:
    client = signed_in_long_ago(user)

    r = post_json(client, "/auth/reauthenticate", {"password": TEST_PASSWORD})

    assert r.status_code == 204
    assert UserSession.objects.get(user=user).second_factor is False


def test_wrong_codes_at_reauthentication_count_for_the_lock(user: User) -> None:
    factor = give_factor(user)
    browser = signed_in_with_code(user, factor)
    for _ in range(5):
        post_json(browser, "/auth/reauthenticate", {"password": TEST_PASSWORD, "code": "000000"})

    r = post_json(
        browser,
        "/auth/reauthenticate",
        {"password": TEST_PASSWORD, "code": factor.code(offset=1)},
    )

    assert (r.status_code, r.json()["code"]) == (429, "second_factor_locked")

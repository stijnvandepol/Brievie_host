"""Security review 2026-11-20 (identity M1): wrong passwords are limited per account too,
not only per email and address. Somebody rotating addresses is slowed; the owner's own
browser is not."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts import signin_limits
from apps.accounts.models import SignInFailures
from tests.factories import TEST_PASSWORD, UserFactory
from tests.helpers import post_json

EMAIL = "jan@example.nl"


def from_address(n: int) -> Client:
    # A new address per attempt, as from a /64 of IPv6 addresses (each its own IPv4 here,
    # since the address limits count a whole /64 as one).
    return Client(REMOTE_ADDR=f"198.51.{n // 250}.{n % 250 + 1}")


def login(client: Client, password: str, email: str = EMAIL) -> int:
    return post_json(client, "/auth/login", {"email": email, "password": password}).status_code


def wrong_from_many_addresses(count: int, email: str = EMAIL) -> list[int]:
    return [login(from_address(n), "fout-wachtwoord", email) for n in range(count)]


@pytest.mark.parametrize(
    ("failures", "wait"),
    [
        (0, timedelta(0)),
        (9, timedelta(0)),
        (10, timedelta(seconds=15)),
        (11, timedelta(seconds=30)),
        (14, timedelta(minutes=4)),
        (16, timedelta(minutes=15)),
        (500, timedelta(minutes=15)),
    ],
)
def test_the_wait_doubles_up_to_a_cap(failures: int, wait: timedelta) -> None:
    assert signin_limits.delay(failures) == wait


def test_after_ten_wrong_passwords_from_anywhere_the_account_waits() -> None:
    UserFactory.create(email=EMAIL)

    assert wrong_from_many_addresses(10) == [401] * 10
    r = post_json(from_address(99), "/auth/login", {"email": EMAIL, "password": TEST_PASSWORD})

    assert r.status_code == 429
    assert r.json() == {
        "code": "too_many_attempts",
        "message": "Te veel pogingen. Probeer het later opnieuw.",
    }
    assert 1 <= int(r["Retry-After"]) <= 16


def test_an_unknown_address_is_slowed_alike() -> None:
    # The answer must not tell whether an account exists.
    wrong_from_many_addresses(10, "niemand@example.nl")

    assert login(from_address(99), "fout-wachtwoord", "niemand@example.nl") == 429


def test_after_the_wait_one_more_try_is_allowed() -> None:
    UserFactory.create(email=EMAIL)
    wrong_from_many_addresses(10)
    SignInFailures.objects.update(last_failure_at=timezone.now() - timedelta(seconds=16))

    assert login(from_address(98), TEST_PASSWORD) == 200
    # A sign-in clears the count.
    assert not SignInFailures.objects.exists()


def test_the_owners_known_browser_is_never_slowed() -> None:
    UserFactory.create(email=EMAIL)
    owner = Client(REMOTE_ADDR="203.0.113.10")
    assert login(owner, TEST_PASSWORD) == 200
    post_json(owner, "/auth/logout")

    wrong_from_many_addresses(30)

    assert login(owner, TEST_PASSWORD) == 200


def test_the_known_browser_cookie_is_signed_and_http_only() -> None:
    UserFactory.create(email=EMAIL)
    client = Client()
    r = post_json(client, "/auth/login", {"email": EMAIL, "password": TEST_PASSWORD})

    cookie = r.cookies[signin_limits.KNOWN_BROWSER_COOKIE]
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"
    assert EMAIL not in cookie.value
    wrong_from_many_addresses(10)
    forged = Client(REMOTE_ADDR="198.51.100.200")
    forged.cookies[signin_limits.KNOWN_BROWSER_COOKIE] = cookie.value + "x"
    assert login(forged, TEST_PASSWORD) == 429


def test_only_a_hash_of_the_email_is_stored() -> None:
    wrong_from_many_addresses(1)

    (row,) = SignInFailures.objects.all()
    assert row.account_key == signin_limits.account_key("JAN@example.nl")
    assert EMAIL not in row.account_key
    assert row.failures == 1


def test_a_count_left_alone_for_a_day_starts_again() -> None:
    UserFactory.create(email=EMAIL)
    wrong_from_many_addresses(12)
    SignInFailures.objects.update(last_failure_at=timezone.now() - timedelta(days=1, seconds=1))

    assert login(from_address(97), "fout-wachtwoord") == 401
    assert SignInFailures.objects.get().failures == 1

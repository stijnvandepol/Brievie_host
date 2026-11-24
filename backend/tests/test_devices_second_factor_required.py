"""After the grace period, an account without a factor is not let in by its paired phone
either (security review of PR #52, point 5): tokens are blocked, not revoked."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User
from apps.devices.models import Device
from apps.devices.services import create_pairing_code, pair_device
from tests.helpers import post_json
from tests.second_factor_support import give_factor


@pytest.fixture
def required(settings: Settings) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    settings.BRIEVIE_SECOND_FACTOR_GRACE_DAYS = 7


def bearer(user: User) -> dict[str, str]:
    _, code = create_pairing_code(user)
    _, token = pair_device(code=code, device_name="iPhone")
    return {"Authorization": f"Bearer {token}"}


def account(days_since: int) -> User:
    from tests.factories import UserFactory

    return UserFactory.create(
        second_factor_required_since=timezone.now() - timedelta(days=days_since)
    )


def test_after_the_grace_a_phone_gets_401_second_factor_required(required: None) -> None:
    user = account(8)
    headers = bearer(user)

    r = Client().get("/api/v1/me", headers=headers)

    assert r.status_code == 401
    assert r.json() == {
        "code": "second_factor_required",
        "message": "Stel eerst tweestapsverificatie in op de webinterface.",
    }
    assert Device.objects.get(user=user).revoked_at is None


def test_within_the_grace_the_phone_works(required: None) -> None:
    user = account(2)

    assert Client().get("/api/v1/me", headers=bearer(user)).status_code == 200


def test_once_the_factor_is_on_the_phone_works_again(required: None) -> None:
    user = account(8)
    headers = bearer(user)
    give_factor(user)

    assert Client().get("/api/v1/me", headers=headers).status_code == 200


def test_after_the_grace_no_pairing_code(required: None) -> None:
    user = account(8)
    browser = Client()
    browser.force_login(user)  # a session from before the grace ended

    r = post_json(browser, "/devices/pairing-codes")

    assert (r.status_code, r.json()["code"]) == (403, "second_factor_required")

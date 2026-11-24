"""The app version a device reports (D 7.1) and how its pairing session signed in."""

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User, UserSession
from apps.devices.models import Device, PairingCode
from apps.devices.services import APP_VERSION_HEADER, clean_app_version
from tests.factories import UserFactory
from tests.helpers import post_json


@pytest.fixture
def user() -> User:
    return UserFactory.create()


@pytest.fixture
def session_client(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


def new_code(session_client: Client) -> str:
    r = post_json(session_client, "/devices/pairing-codes")
    assert r.status_code == 201
    code: str = r.json()["code"]
    return code


def pair(session_client: Client, **extra: str) -> tuple[Device, str]:
    body = {"code": new_code(session_client), "device_name": "iPhone", **extra}
    r = post_json(Client(), "/devices/pair", body)
    assert r.status_code == 201, r.content
    return Device.objects.get(id=r.json()["device_id"]), r.json()["token"]


def me(token: str, version: str | None = None) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    if version is not None:
        headers[APP_VERSION_HEADER] = version
    return Client().get("/api/v1/me", headers=headers).status_code


def test_pairing_stores_the_app_version(session_client: Client) -> None:
    device, _ = pair(session_client, app_version="1.4.0 (12)")

    assert device.app_version == "1.4.0 (12)"
    assert device.app_version_at is not None


def test_a_1c_pair_body_without_app_version_still_pairs(session_client: Client) -> None:
    r = post_json(
        Client(), "/devices/pair", {"code": new_code(session_client), "device_name": "iPhone"}
    )

    assert r.status_code == 201
    assert set(r.json()) == {"device_id", "token", "user", "public_urls"}
    device = Device.objects.get(id=r.json()["device_id"])
    assert device.app_version == ""
    assert device.app_version_at is None


def test_header_refreshes_the_version_at_most_once_a_day(session_client: Client) -> None:
    device, token = pair(session_client)

    assert me(token, "1.5.0") == 200
    device.refresh_from_db()
    assert device.app_version == "1.5.0"
    assert device.app_version_at is not None

    Device.objects.filter(pk=device.pk).update(app_version_at=timezone.now() - timedelta(hours=1))
    assert me(token, "1.5.1") == 200
    device.refresh_from_db()
    assert device.app_version == "1.5.0"

    Device.objects.filter(pk=device.pk).update(app_version_at=timezone.now() - timedelta(hours=25))
    assert me(token, "1.5.1") == 200
    device.refresh_from_db()
    assert device.app_version == "1.5.1"


def test_the_same_version_writes_nothing(session_client: Client) -> None:
    device, token = pair(session_client, app_version="1.5.0")
    old = timezone.now() - timedelta(days=3)
    Device.objects.filter(pk=device.pk).update(app_version_at=old)

    assert me(token, "1.5.0") == 200

    device.refresh_from_db()
    assert device.app_version_at == old


def test_a_request_without_the_header_keeps_working(session_client: Client) -> None:
    device, token = pair(session_client, app_version="1.5.0")

    assert me(token) == 200
    device.refresh_from_db()
    assert device.app_version == "1.5.0"


def test_a_malformed_header_is_ignored(session_client: Client) -> None:
    device, token = pair(session_client)
    assert me(token, "1.5.0") == 200
    Device.objects.filter(pk=device.pk).update(app_version_at=timezone.now() - timedelta(days=2))

    assert me(token, "<script>") == 200
    assert me(token, "1" * 51) == 200

    device.refresh_from_db()
    assert device.app_version == "1.5.0"


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("1.4.0 (12)", "1.4.0 (12)"),
        ("  2.0.0-beta+3  ", "2.0.0-beta+3"),
        ("", ""),
        ("   ", ""),
        ("<script>", ""),
        ("1.0\n2", ""),
        ("1" * 50, "1" * 50),
        ("1" * 51, ""),
        ("1.0;drop", ""),
    ],
)
def test_clean_app_version(raw: str, clean: str) -> None:
    assert clean_app_version(raw) == clean


def test_a_malformed_body_version_is_stored_empty(session_client: Client) -> None:
    device, _ = pair(session_client, app_version="<b>1</b>")

    assert device.app_version == ""


def test_list_devices_shows_the_app_version(session_client: Client) -> None:
    pair(session_client, app_version="1.4.0 (12)")

    r = session_client.get("/api/v1/devices")

    assert r.status_code == 200
    assert [d["app_version"] for d in r.json()] == ["1.4.0 (12)"]


def test_the_device_remembers_how_its_pairing_session_signed_in(session_client: Client) -> None:
    device, _ = pair(session_client)
    assert PairingCode.objects.get(used_at__isnull=False).paired_via == "password"
    assert device.paired_via == "password"

    UserSession.objects.update(method=UserSession.Method.SSO)
    sso_device, _ = pair(session_client)

    assert sso_device.paired_via == "sso"
    assert PairingCode.objects.latest("created_at").paired_via == "sso"

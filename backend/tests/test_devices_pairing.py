import json
import re
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.db import connection
from django.test import Client
from django.utils import timezone
from pytest_django import Settings

from apps.accounts.models import User
from apps.devices.models import Device, PairingCode
from apps.devices.services import create_pairing_code, pair_device
from tests.factories import UserFactory

CODE_FORMAT = re.compile(
    r"^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}$"
)


def post(client: Client, path: str, body: dict[str, Any] | None = None) -> Any:  # noqa: ANN401
    return client.post(f"/api/v1{path}", body or {}, content_type="application/json")


@pytest.fixture
def user() -> User:
    return UserFactory.create()


@pytest.fixture
def session_client(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


def new_code(session_client: Client) -> str:
    r = post(session_client, "/devices/pairing-codes")
    assert r.status_code == 201
    code: str = r.json()["code"]
    return code


def test_create_pairing_code_returns_code_and_qr_payload(
    session_client: Client, settings: Settings
) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local", "https://brievie.example.nl"]

    r = post(session_client, "/devices/pairing-codes")

    assert r.status_code == 201
    body = r.json()
    assert CODE_FORMAT.match(body["code"])
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert timedelta(minutes=9) < expires_at - timezone.now() <= timedelta(minutes=10)
    assert isinstance(body["qr_payload"], str)
    assert json.loads(body["qr_payload"]) == {
        "v": 1,
        "urls": ["https://brievie.local", "https://brievie.example.nl"],
        "code": body["code"],
        "ca_sha256": None,
    }


def test_happy_path_pair_and_use_the_token(session_client: Client, user: User) -> None:
    code = new_code(session_client)

    r = post(Client(), "/devices/pair", {"code": code, "device_name": "iPhone van Anna"})

    assert r.status_code == 201
    body = r.json()
    assert body["user"]["id"] == str(user.id)
    device = Device.objects.get(id=body["device_id"])
    assert device.user == user
    assert device.name == "iPhone van Anna"
    me = Client().get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200
    assert me.json()["id"] == str(user.id)


def test_code_is_single_use(session_client: Client) -> None:
    code = new_code(session_client)
    assert post(Client(), "/devices/pair", {"code": code, "device_name": "A"}).status_code == 201

    r = post(Client(), "/devices/pair", {"code": code, "device_name": "B"})

    assert r.status_code == 400
    assert r.json()["code"] == "pairing_code_invalid"
    assert "token" not in r.json()
    assert Device.objects.count() == 1


def test_expired_code_is_rejected(session_client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    code = new_code(session_client)
    later = timezone.now() + timedelta(minutes=11)
    monkeypatch.setattr(timezone, "now", lambda: later)

    r = post(Client(), "/devices/pair", {"code": code, "device_name": "A"})

    assert r.status_code == 400
    assert r.json()["code"] == "pairing_code_invalid"
    assert Device.objects.count() == 0


def test_code_is_accepted_in_lower_case_and_without_dash(session_client: Client) -> None:
    code = new_code(session_client)

    r = post(Client(), "/devices/pair", {"code": code.replace("-", "").lower(), "device_name": "A"})

    assert r.status_code == 201


def test_wrong_code_gives_no_token(session_client: Client) -> None:
    new_code(session_client)

    r = post(Client(), "/devices/pair", {"code": "AAAA-AAAA", "device_name": "A"})

    assert r.status_code == 400
    assert r.json() == {"code": "pairing_code_invalid", "message": r.json()["message"]}
    assert Device.objects.count() == 0


def test_new_code_invalidates_older_unused_codes(session_client: Client) -> None:
    first = new_code(session_client)
    second = new_code(session_client)

    assert post(Client(), "/devices/pair", {"code": first, "device_name": "A"}).status_code == 400
    assert post(Client(), "/devices/pair", {"code": second, "device_name": "A"}).status_code == 201


def test_pairing_is_rate_limited_per_address(session_client: Client) -> None:
    code = new_code(session_client)
    attacker = Client(REMOTE_ADDR="198.51.100.9")
    for _ in range(10):
        assert (
            post(attacker, "/devices/pair", {"code": "AAAA-AAAA", "device_name": "A"}).status_code
            == 400
        )

    r = post(attacker, "/devices/pair", {"code": code, "device_name": "A"})

    assert r.status_code == 429
    assert r.json()["code"] == "too_many_attempts"
    assert int(r["Retry-After"]) > 0
    # Another address is not affected.
    assert post(Client(), "/devices/pair", {"code": code, "device_name": "A"}).status_code == 201


def test_create_pairing_code_needs_a_session_not_a_bearer_token(session_client: Client) -> None:
    token = post(Client(), "/devices/pair", {"code": new_code(session_client), "device_name": "A"})
    bearer = token.json()["token"]

    r = Client().post(
        "/api/v1/devices/pairing-codes", headers={"Authorization": f"Bearer {bearer}"}
    )

    assert r.status_code == 401
    assert r.json()["code"] == "not_authenticated"


def test_create_pairing_code_unauthenticated_is_401(client: Client) -> None:
    assert post(client, "/devices/pairing-codes").status_code == 401


def test_list_devices_shows_no_secrets(session_client: Client, user: User) -> None:
    paired = post(Client(), "/devices/pair", {"code": new_code(session_client), "device_name": "A"})
    token = paired.json()["token"]
    other = UserFactory.create()
    Device.objects.create(user=other, name="Niet van mij", token_hash="0" * 64)

    r = session_client.get("/api/v1/devices")

    assert r.status_code == 200
    body = r.json()
    assert [d["name"] for d in body] == ["A"]
    assert set(body[0]) == {"id", "name", "created_at", "last_seen_at"}
    text = r.content.decode()
    assert "token" not in text
    assert token not in text
    assert Device.objects.get(user=user).token_hash not in text


def test_revoke_device(session_client: Client, user: User) -> None:
    paired = post(Client(), "/devices/pair", {"code": new_code(session_client), "device_name": "A"})
    device_id = paired.json()["device_id"]

    r = session_client.delete(f"/api/v1/devices/{device_id}")

    assert r.status_code == 204
    assert Device.objects.get(id=device_id).revoked_at is not None
    assert session_client.get("/api/v1/devices").json() == []


def test_revoking_another_users_device_is_404(session_client: Client) -> None:
    other = Device.objects.create(user=UserFactory.create(), name="B", token_hash="1" * 64)

    r = session_client.delete(f"/api/v1/devices/{other.id}")

    assert r.status_code == 404
    assert r.json()["code"] == "device_not_found"
    other.refresh_from_db()
    assert other.revoked_at is None


def test_database_holds_neither_plain_code_nor_plain_token(user: User) -> None:
    _, code = create_pairing_code(user)
    _, token = pair_device(code=code, device_name="A")

    secrets = {code, code.replace("-", ""), token}
    with connection.cursor() as c:
        for table in connection.introspection.table_names():
            c.execute(f'SELECT * FROM "{table}"')  # noqa: S608 - names from introspection
            for row in c.fetchall():
                for value in row:
                    assert not any(s in str(value) for s in secrets), table
    assert PairingCode.objects.get().code_hash != code

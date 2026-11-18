"""The per-code pairing link (D 6.2, D 20): addresses, CA pin and pairing id, no secret;
and the audit rows of pairing and revoking."""

from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import User
from apps.audit.models import ServerAuditEvent
from apps.devices import pki
from apps.devices.models import PairingCode
from apps.devices.services import server_link
from tests.factories import UserFactory
from tests.helpers import post_json


@pytest.fixture
def user() -> User:
    return UserFactory.create()


@pytest.fixture
def session_client(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


def link_fields(link: str) -> list[tuple[str, str]]:
    parts = urlsplit(link)
    assert (parts.scheme, parts.netloc, parts.path, parts.query) == ("brievie", "server", "", "")
    return parse_qsl(parts.fragment)


def test_server_link_carries_addresses_pin_and_pairing_id_only(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local", "https://192.168.1.4"]
    monkeypatch.setattr(pki, "ca_certificate_sha256", lambda: "ab" * 32)

    link = server_link(UUID("0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70"))

    assert link == (
        "brievie://server#v=1&url=https%3A%2F%2Fbrievie.local&url=https%3A%2F%2F192.168.1.4"
        "&ca_sha256=" + "ab" * 32 + "&pairing=0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70"
    )


def test_server_link_leaves_out_the_pin_without_a_private_ca(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.example.nl"]

    link = server_link(UUID("0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70"))

    assert link_fields(link) == [
        ("v", "1"),
        ("url", "https://brievie.example.nl"),
        ("pairing", "0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70"),
    ]


def test_create_pairing_code_returns_the_link_of_its_own_row(
    session_client: Client, settings: Settings
) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local"]

    r = post_json(session_client, "/devices/pairing-codes")

    assert r.status_code == 201
    fields = dict(link_fields(r.json()["server_link"]))
    assert fields["pairing"] == str(PairingCode.objects.get().id)
    assert fields["url"] == "https://brievie.local"


def test_server_link_has_no_pairing_code(session_client: Client) -> None:
    body = post_json(session_client, "/devices/pairing-codes").json()
    code: str = body["code"]

    link = body["server_link"]

    assert link.startswith("brievie://server#")
    for secret in (code, code.replace("-", ""), code.lower(), code.replace("-", "").lower()):
        assert secret not in link
    assert {name for name, _ in link_fields(link)} <= {"v", "url", "ca_sha256", "pairing"}


def test_each_code_gets_its_own_link(session_client: Client) -> None:
    first = post_json(session_client, "/devices/pairing-codes").json()["server_link"]
    second = post_json(session_client, "/devices/pairing-codes").json()["server_link"]

    assert dict(link_fields(first))["pairing"] != dict(link_fields(second))["pairing"]


def test_the_pairing_code_response_is_not_stored(session_client: Client) -> None:
    r = post_json(session_client, "/devices/pairing-codes")

    assert r.status_code == 201
    assert r["Cache-Control"] == "no-store"


def test_pairing_and_revoking_are_logged(session_client: Client, user: User) -> None:
    code = post_json(session_client, "/devices/pairing-codes").json()["code"]
    paired = post_json(
        Client(REMOTE_ADDR="192.0.2.7"), "/devices/pair", {"code": code, "device_name": "iPhone"}
    )
    assert paired.status_code == 201
    device_id = paired.json()["device_id"]

    [row] = ServerAuditEvent.objects.filter(code="device_paired")
    assert row.values == {"via": "code"}
    assert (row.actor_kind, row.actor_id, row.actor_name) == ("user", user.id, user.name)
    assert row.client_ip == "192.0.2.7"
    assert (row.target_kind, str(row.target_id), row.target_name) == (
        "device",
        device_id,
        "iPhone",
    )

    assert session_client.delete(f"/api/v1/devices/{device_id}").status_code == 204

    [revoked] = ServerAuditEvent.objects.filter(code="device_revoked")
    assert (revoked.actor_kind, revoked.actor_id) == ("user", user.id)
    assert (revoked.target_kind, str(revoked.target_id)) == ("device", device_id)
    assert revoked.values == {}


def test_revoking_an_unknown_device_logs_nothing(session_client: Client) -> None:
    r = session_client.delete("/api/v1/devices/0b7e2f4c-5d1a-4e8b-9c3f-6a2d8e1f4b70")

    assert r.status_code == 404
    assert not ServerAuditEvent.objects.filter(code="device_revoked").exists()


def test_making_pairing_codes_is_limited_per_user(session_client: Client) -> None:
    for _ in range(10):
        assert post_json(session_client, "/devices/pairing-codes").status_code == 201

    r = post_json(session_client, "/devices/pairing-codes")

    assert r.status_code == 429
    assert r.json()["code"] == "too_many_attempts"
    other = Client()
    other.force_login(UserFactory.create())
    assert post_json(other, "/devices/pairing-codes").status_code == 201

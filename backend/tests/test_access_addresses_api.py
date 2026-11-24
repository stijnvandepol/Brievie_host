"""Paired apps learn which addresses carry a public certificate (S 9, plan Task 7)."""

import json

import pytest
from django.test import Client
from pytest_django import Settings

from apps.accounts.models import User
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.members_support import bearer, paired
from tests.test_devices_pairing_proof import HOST, challenge, issue, prove, send

HOME = "https://192.168.1.20:443"
PUBLIC = f"https://{HOST}"


@pytest.fixture
def user() -> User:
    return UserFactory.create()


@pytest.fixture
def session_client(client: Client, user: User) -> Client:
    client.force_login(user)
    return client


@pytest.fixture
def with_public_name(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = [HOME, PUBLIC]
    settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS = [PUBLIC]


@pytest.mark.usefixtures("with_public_name")
def test_pair_and_pair_with_proof_return_the_public_urls(session_client: Client) -> None:
    code = post_json(session_client, "/devices/pairing-codes").json()["code"]
    paired_by_code = post_json(Client(), "/devices/pair", {"code": code, "device_name": "iPhone"})
    assert paired_by_code.status_code == 201
    assert paired_by_code.json()["public_urls"] == [PUBLIC]

    code, pairing_id = issue(session_client)
    nonce = challenge()
    by_proof = send(pairing_id, nonce, prove(code, pairing_id, nonce))
    assert by_proof.status_code == 201, by_proof.content
    assert by_proof.json()["public_urls"] == [PUBLIC]


def test_pair_returns_empty_public_urls_without_a_public_name(
    session_client: Client, settings: Settings
) -> None:
    settings.BRIEVIE_PUBLIC_URLS = [HOME]
    settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS = []
    code = post_json(session_client, "/devices/pairing-codes").json()["code"]
    response = post_json(Client(), "/devices/pair", {"code": code, "device_name": "iPhone"})
    assert response.json()["public_urls"] == []


@pytest.mark.usefixtures("with_public_name")
def test_get_server_addresses_with_device_token(user: User) -> None:
    _, token = paired(user)
    response = Client().get("/api/v1/server/addresses", headers=bearer(token))
    assert response.status_code == 200
    assert response.json() == {"urls": [HOME, PUBLIC], "public_urls": [PUBLIC]}


@pytest.mark.usefixtures("with_public_name")
def test_get_server_addresses_with_session(session_client: Client) -> None:
    response = session_client.get("/api/v1/server/addresses")
    assert response.status_code == 200
    assert response.json()["public_urls"] == [PUBLIC]


def test_get_server_addresses_without_auth_is_401() -> None:
    response = Client().get("/api/v1/server/addresses")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


@pytest.mark.usefixtures("with_public_name")
def test_qr_payload_is_unchanged(session_client: Client) -> None:
    body = post_json(session_client, "/devices/pairing-codes").json()
    payload = json.loads(body["qr_payload"])
    assert set(payload) == {"v", "urls", "code", "ca_sha256"}
    assert payload["v"] == 1
    assert payload["urls"] == [HOME, PUBLIC]

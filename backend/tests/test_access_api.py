"""getExternalAccess: server admins, web session only, no secret, no transaction (S 8)."""

import secrets

import pytest
from django.test import Client
from django.urls import resolve
from pytest_django import Settings

from apps.workspaces.models import Membership
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.members_support import as_user, bearer, paired

PATH = "/api/v1/server/external-access"
# Made at run time: no token-like literal in the repository.
TOKEN_LIKE = secrets.token_urlsafe(32)


def test_get_external_access_server_admin_only() -> None:
    owner = UserFactory.create()
    workspace = create_workspace(name="Thuis", kind="home", admin=owner)
    assert Membership.objects.get(user=owner, workspace=workspace).role == Membership.Role.ADMIN
    response = as_user(owner).get(PATH)
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_get_external_access_device_token_gets_401() -> None:
    admin = UserFactory.create(is_server_admin=True)
    _, token = paired(admin)
    response = Client().get(PATH, headers=bearer(token))
    assert response.status_code == 401
    assert Client().get(PATH).status_code == 401


def test_get_external_access_no_store_and_shape(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIEVIE_TUNNEL_TOKEN", TOKEN_LIKE)
    settings.BRIEVIE_HOSTNAME = "192.168.1.20"
    settings.BRIEVIE_PUBLIC_URLS = ["https://192.168.1.20:443"]
    settings.BRIEVIE_TUNNEL_TOKEN_SET = False
    admin = UserFactory.create(is_server_admin=True)
    response = as_user(admin).get(PATH)
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"
    body = response.json()
    assert body["tunnel"] == {"token_set": False, "connected": "not_set"}
    assert body["addresses"] == [
        {"url": "https://192.168.1.20:443", "kind": "home", "certificate": "private_ca"}
    ]
    assert body["docs_url"] == "https://brievie.com/docs/installeren#van-buiten"
    assert body["second_factor_available"] is False
    assert TOKEN_LIKE not in response.content.decode()


def test_probe_runs_outside_a_transaction() -> None:
    assert resolve(PATH).func._non_atomic_requests == {"default"}  # type: ignore[attr-defined]

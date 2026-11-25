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
from tests.second_factor_support import give_factor, signed_in_with_code

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
    assert body["second_factor_available"] is True
    assert TOKEN_LIKE not in response.content.decode()


def test_probe_runs_outside_a_transaction() -> None:
    assert resolve(PATH).func._non_atomic_requests == {"default"}  # type: ignore[attr-defined]


def test_the_page_names_a_server_admin_without_a_second_factor(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.BRIEVIE_HOSTNAME = "192.168.1.20"
    settings.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl"
    settings.BRIEVIE_PUBLIC_URLS = ["https://192.168.1.20:443", "https://brievie.example.nl"]
    settings.BRIEVIE_TUNNEL_TOKEN_SET = True
    settings.BRIEVIE_PUBLIC_WEB = True
    admin = UserFactory.create(is_server_admin=True)
    client = signed_in_with_code(admin, give_factor(admin))
    # Behind Caddy, opened at home: only then does the public route ask the second step.
    from apps.common import net

    monkeypatch.setattr(net, "caddy_addresses", lambda **_kwargs: frozenset({"127.0.0.1"}))
    settings.BRIEVIE_BEHIND_CADDY = True

    def codes() -> list[str]:
        response = client.get(PATH, headers={"X-Brievie-Route": "private"})
        return [check["code"] for check in response.json()["checks"]]

    assert "public_web_second_factor_required" in codes()
    assert "public_web_without_second_factor" not in codes()
    assert "public_web_admin_without_second_factor" not in codes()
    other = UserFactory.create(is_server_admin=True)
    assert "public_web_admin_without_second_factor" in codes()
    # A switched-off admin does not count.
    other.is_active = False
    other.save(update_fields=["is_active"])
    assert "public_web_admin_without_second_factor" not in codes()

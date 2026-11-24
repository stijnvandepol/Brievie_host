"""The route a request came by, as Caddy marks it (S 5.6, 7.3)."""

import pytest
from django.http import HttpRequest
from django.test import RequestFactory
from pytest_django import Settings

from apps.access.route import is_public_route, peer_ip

CADDY_PEERS = {"172.18.0.5"}


@pytest.fixture(autouse=True)
def _caddy_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caddy is the peer of these requests (apps.common.net.from_caddy)."""
    from apps.common import net

    monkeypatch.setattr(net, "caddy_addresses", lambda: frozenset(CADDY_PEERS))


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: nothing here uses the database."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def request_with(**meta: str) -> HttpRequest:
    request = RequestFactory().get("/", REMOTE_ADDR="172.18.0.5")
    request.META.update(meta)
    return request


@pytest.mark.parametrize(("header", "public"), [("private", False), ("public", True)])
def test_behind_caddy_the_route_header_decides(
    settings: Settings, header: str, public: bool
) -> None:
    settings.BRIEVIE_BEHIND_CADDY = True
    assert is_public_route(request_with(HTTP_X_BRIEVIE_ROUTE=header)) is public


@pytest.mark.parametrize("header", ["", "Private", "privat", "private, private", "anything"])
def test_missing_route_header_counts_as_public(settings: Settings, header: str) -> None:
    # Caddy always sets it; anything that is not exactly "private" is the closed way.
    settings.BRIEVIE_BEHIND_CADDY = True
    assert is_public_route(request_with()) is True
    assert is_public_route(request_with(HTTP_X_BRIEVIE_ROUTE=header)) is True


def test_route_header_ignored_when_not_behind_caddy(settings: Settings) -> None:
    settings.BRIEVIE_BEHIND_CADDY = False
    assert is_public_route(request_with(HTTP_X_BRIEVIE_ROUTE="public")) is False
    assert is_public_route(request_with()) is False


def test_peer_ip_behind_caddy_is_caddys_peer(settings: Settings) -> None:
    settings.BRIEVIE_BEHIND_CADDY = True
    assert peer_ip(request_with(HTTP_X_BRIEVIE_PEER_IP="192.168.1.5")) == "192.168.1.5"
    assert peer_ip(request_with()) == ""


def test_peer_ip_without_caddy_is_the_remote_address(settings: Settings) -> None:
    settings.BRIEVIE_BEHIND_CADDY = False
    assert peer_ip(request_with(HTTP_X_BRIEVIE_PEER_IP="192.168.1.5")) == "172.18.0.5"

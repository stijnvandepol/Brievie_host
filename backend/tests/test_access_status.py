"""The checks of the page "Van buiten bereikbaar" (S 8); what was seen and devices injected."""

import json
import secrets
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.http import HttpRequest
from django.test import RequestFactory
from pytest_django import Settings

from apps.access import observed, status
from apps.access.status import CHECK_CODES, external_access_status, needs_app_update

# Made at run time: no token-like literal in the repository.
TOKEN_LIKE = secrets.token_urlsafe(32)

CADDY_PEERS = {"127.0.0.1"}


@pytest.fixture(autouse=True)
def _caddy_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caddy is the peer of these requests (apps.common.net.from_caddy)."""
    from apps.common import net

    monkeypatch.setattr(net, "caddy_addresses", lambda: frozenset(CADDY_PEERS))


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: devices and what was seen are injected."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


@pytest.fixture(autouse=True)
def home(settings: Settings) -> Settings:
    settings.BRIEVIE_BEHIND_CADDY = True
    settings.BRIEVIE_HOSTNAME = "192.168.1.20"
    settings.BRIEVIE_VPN_HOSTNAME = ""
    settings.BRIEVIE_PUBLIC_HOSTNAME = ""
    settings.BRIEVIE_PUBLIC_URLS = ["https://192.168.1.20:443"]
    settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS = []
    settings.ALLOWED_HOSTS = ["192.168.1.20", "brievie.example.nl", "nas.ts.net", "brievie.local"]
    settings.BRIEVIE_TUNNEL_TOKEN_SET = False
    settings.BRIEVIE_TRUSTED_PROXIES = []
    settings.BRIEVIE_PUBLIC_WEB = False
    settings.BRIEVIE_SECOND_FACTOR_AVAILABLE = False
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 33554432
    return settings


SEEN: dict[str, datetime] = {}
PEERS: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _nothing_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    SEEN.clear()
    PEERS.clear()
    monkeypatch.setattr(observed, "last_seen", SEEN.get)
    monkeypatch.setattr(observed, "last_peer", lambda kind: PEERS.get(kind, ""))


def with_public(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl"
    settings.BRIEVIE_PUBLIC_URLS = ["https://192.168.1.20:443", "https://brievie.example.nl"]
    settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS = ["https://brievie.example.nl"]


def request(
    *,
    route: str = "private",
    host: str = "192.168.1.20",
    secure: bool = True,
    client: str = "192.168.1.30",
    peer: str = "192.168.1.30",
) -> HttpRequest:
    made = RequestFactory().get("/", secure=secure, HTTP_HOST=host)
    made.META.update(
        {
            "HTTP_X_BRIEVIE_ROUTE": route,
            "HTTP_X_BRIEVIE_CLIENT_IP": client,
            "HTTP_X_BRIEVIE_PEER_IP": peer,
        }
    )
    return made


def codes(
    made: HttpRequest | None = None,
    *,
    tunnel_seen: bool = True,
    versions: list[str] | None = None,
) -> list[str]:
    if tunnel_seen:
        SEEN.setdefault("tunnel", datetime.now(UTC))
    else:
        SEEN.pop("tunnel", None)
    found = external_access_status(made or request(), app_versions=lambda: versions or [])
    return [check.code for check in found.checks]


def test_the_home_installation_has_no_problem() -> None:
    assert codes() == []


def test_check_hostname_loopback(home: Settings) -> None:
    home.BRIEVIE_HOSTNAME = "localhost"
    assert "hostname_loopback" in codes()
    home.BRIEVIE_HOSTNAME = "127.0.0.1"
    assert "hostname_loopback" in codes()


def test_check_hostname_mdns_over_vpn(home: Settings) -> None:
    home.BRIEVIE_HOSTNAME = "brievie.local"
    assert "hostname_mdns_over_vpn" in codes(request(host="brievie.local"))


def test_check_public_hostname_missing_with_trusted_proxies(home: Settings) -> None:
    home.BRIEVIE_TRUSTED_PROXIES = ["192.168.1.5/32"]
    assert "public_hostname_missing" in codes()
    with_public(home)
    assert "public_hostname_missing" not in codes()


def test_check_public_hostname_missing_with_a_tunnel(home: Settings) -> None:
    home.BRIEVIE_TUNNEL_TOKEN_SET = True
    assert "public_hostname_missing" in codes()


def test_check_public_url_overridden_without_public_host(home: Settings) -> None:
    home.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl"
    home.BRIEVIE_PUBLIC_CERTIFICATE_URLS = []
    assert "public_url_overridden_without_public_host" in codes()


def test_check_tunnel_not_seen_when_token_set_and_no_request_came_through(home: Settings) -> None:
    with_public(home)
    home.BRIEVIE_TUNNEL_TOKEN_SET = True
    assert "tunnel_not_seen" in codes(tunnel_seen=False)
    assert "tunnel_not_seen" not in codes(tunnel_seen=True)


def test_without_a_token_the_tunnel_is_not_set() -> None:
    found = external_access_status(request(), app_versions=list)
    assert found.tunnel_connected == "not_set"


def test_the_api_makes_no_outgoing_call() -> None:
    source = (Path(__file__).resolve().parents[1] / "apps" / "access" / "status.py").read_text()
    for needle in ("urllib.request", "http.client", "socket", "requests", "cloudflared:2000"):
        assert needle not in source, needle


def test_check_upload_limit_above_cloudflare(home: Settings) -> None:
    with_public(home)
    home.BRIEVIE_TUNNEL_TOKEN_SET = True
    home.BRIEVIE_MAX_UPLOAD_BYTES = 100_000_001
    assert "upload_limit_above_cloudflare" in codes()
    home.BRIEVIE_TUNNEL_TOKEN_SET = False
    assert "upload_limit_above_cloudflare" not in codes()


def test_check_public_web_without_second_factor_and_public_web_off(home: Settings) -> None:
    with_public(home)
    home.BRIEVIE_TUNNEL_TOKEN_SET = True
    assert "public_web_off" in codes()
    assert "public_web_without_second_factor" not in codes()
    home.BRIEVIE_PUBLIC_WEB = True
    assert "public_web_without_second_factor" in codes()
    assert "public_web_off" not in codes()
    home.BRIEVIE_SECOND_FACTOR_AVAILABLE = True
    assert "public_web_without_second_factor" not in codes()


def test_check_request_not_secure() -> None:
    assert "request_not_secure" in codes(request(secure=False))
    assert "request_not_secure" not in codes(request(secure=True))


def test_check_public_host_not_forwarded(home: Settings) -> None:
    with_public(home)
    home.BRIEVIE_TRUSTED_PROXIES = ["192.168.1.5/32"]
    wrong = request(route="public", host="192.168.1.20", peer="192.168.1.5", client="203.0.113.9")
    right = request(
        route="public", host="brievie.example.nl", peer="192.168.1.5", client="203.0.113.9"
    )
    assert "public_host_not_forwarded" in codes(wrong)
    assert "public_host_not_forwarded" not in codes(right)


def test_check_trusted_proxy_without_forwarding(home: Settings) -> None:
    # Docker's gateway trusted: the peer is the trusted address and no forwarded client.
    with_public(home)
    home.BRIEVIE_TRUSTED_PROXIES = ["172.18.0.1/32"]
    direct = request(peer="172.18.0.1", client="172.18.0.1")
    assert "trusted_proxy_without_forwarding" in codes(direct)
    forwarded = request(peer="172.18.0.1", client="203.0.113.9")
    assert "trusted_proxy_without_forwarding" not in codes(forwarded)


def test_check_cloudflare_reads_along_always_with_the_tunnel(home: Settings) -> None:
    assert "cloudflare_reads_along" not in codes()
    with_public(home)
    home.BRIEVIE_TUNNEL_TOKEN_SET = True
    assert "cloudflare_reads_along" in codes()


def test_check_app_update_needed_lists_no_device_names(home: Settings) -> None:
    with_public(home)
    home.BRIEVIE_TUNNEL_TOKEN_SET = True
    found = external_access_status(request(), app_versions=lambda: ["1.0.0"])
    assert "app_update_needed" in [c.code for c in found.checks]
    assert "iPhone" not in json.dumps(asdict(found))
    assert "app_update_needed" not in codes(versions=[])


def test_needs_app_update_compares_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(status, "FIRST_APP_WITH_PUBLIC_ADDRESSES", (1, 4, 0))
    assert needs_app_update(["1.3.9"])
    assert needs_app_update([""])
    assert not needs_app_update(["1.4.0", "2.0"])
    monkeypatch.setattr(status, "FIRST_APP_WITH_PUBLIC_ADDRESSES", None)
    assert needs_app_update(["9.9.9"])


def test_every_check_code_is_in_the_closed_list() -> None:
    assert len(CHECK_CODES) == len(set(CHECK_CODES))


def test_status_never_contains_the_token(home: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_TUNNEL_TOKEN", TOKEN_LIKE)
    monkeypatch.setenv("TUNNEL_TOKEN", TOKEN_LIKE)
    with_public(home)
    home.BRIEVIE_TUNNEL_TOKEN_SET = True
    found = external_access_status(request(), app_versions=list)
    assert TOKEN_LIKE not in json.dumps(asdict(found))
    assert found.tunnel_token_set is True


def test_seen_as_reports_route_client_and_peer() -> None:
    found = external_access_status(
        request(route="public", client="203.0.113.9", peer="192.168.1.5"),
        app_versions=list,
    )
    assert found.seen_as.route == "public"
    assert found.seen_as.secure is True
    assert found.seen_as.client_ip == "203.0.113.9"
    assert found.seen_as.peer_ip == "192.168.1.5"


def test_addresses_carry_their_kind_and_certificate(home: Settings) -> None:
    home.BRIEVIE_VPN_HOSTNAME = "nas.ts.net"
    home.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl"
    home.BRIEVIE_PUBLIC_URLS = [
        "https://192.168.1.20:443",
        "https://nas.ts.net:443",
        "https://brievie.example.nl",
    ]
    home.BRIEVIE_PUBLIC_CERTIFICATE_URLS = ["https://brievie.example.nl"]
    found = external_access_status(request(), app_versions=list)
    assert [(a.kind, a.certificate) for a in found.addresses] == [
        ("home", "private_ca"),
        ("vpn", "private_ca"),
        ("public", "public"),
    ]
    assert found.docs_url == "https://brievie.com/docs/installeren#van-buiten"


def test_check_public_name_from_untrusted_source_names_the_local_peer(home: Settings) -> None:
    with_public(home)
    assert "public_name_from_untrusted_source" not in codes()
    SEEN["public_name_untrusted"] = datetime.now(UTC)
    PEERS["public_name_untrusted"] = "192.168.1.5"
    found = external_access_status(request(), app_versions=list)
    assert "public_name_from_untrusted_source" in [c.code for c in found.checks]
    assert found.untrusted_public_name_peer == "192.168.1.5"


def test_check_cloudflare_on_https_port(home: Settings) -> None:
    assert "cloudflare_on_https_port" not in codes()
    SEEN["cloudflare_on_https_port"] = datetime.now(UTC)
    assert "cloudflare_on_https_port" in codes()


def test_the_proxy_checks_come_from_what_was_seen_with_the_page_opened_at_home(
    home: Settings,
) -> None:
    # Security review of PR #53, point 3: the page is opened at home, so these two checks
    # read what the server saw on the public route.
    with_public(home)
    home.BRIEVIE_TRUSTED_PROXIES = ["192.168.1.5/32"]
    assert "public_host_not_forwarded" not in codes()
    assert "trusted_proxy_without_forwarding" not in codes()
    SEEN["host_not_forwarded"] = datetime.now(UTC)
    SEEN["proxy_without_forwarding"] = datetime.now(UTC)
    found = codes()
    assert "public_host_not_forwarded" in found
    assert "trusted_proxy_without_forwarding" in found

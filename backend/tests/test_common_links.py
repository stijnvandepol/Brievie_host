import hashlib

import pytest
from django.test import RequestFactory
from pytest_django import Settings

from apps.common.links import fragment_link, home_network_only, public_base_url, web_urls
from apps.common.tokens import new_token, token_hash


@pytest.mark.parametrize(
    ("urls", "expected"),
    [
        (["https://brievie.local"], True),
        (["https://192.168.1.4"], True),
        (["https://brievie.local", "https://post.example.nl"], False),
        ([], True),
        (["https://brievie.lan:8443"], True),
        (["https://nas.home.arpa"], True),
        (["https://localhost"], True),
        (["https://[fe80::1]"], True),
        (["https://10.0.0.2"], True),
        (["https://8.8.8.8"], False),
        (["https://brievie.local.example.nl"], False),
    ],
)
def test_home_network_only(
    rf: RequestFactory, settings: Settings, urls: list[str], expected: bool
) -> None:
    settings.BRIEVIE_PUBLIC_URLS = urls
    settings.ALLOWED_HOSTS = ["brievie.local"]

    assert home_network_only(rf.get("/", HTTP_HOST="brievie.local")) is expected


def test_without_a_public_url_the_request_host_decides(
    rf: RequestFactory, settings: Settings
) -> None:
    settings.BRIEVIE_PUBLIC_URLS = []
    settings.ALLOWED_HOSTS = ["post.example.nl"]

    assert home_network_only(rf.get("/", HTTP_HOST="post.example.nl")) is False


def test_public_base_url_and_fragment_link(rf: RequestFactory, settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://brievie.local/", "https://post.example.nl"]
    request = rf.get("/")

    assert public_base_url(request) == "https://brievie.local"
    assert fragment_link(request, "/pad", "abc") == "https://brievie.local/pad#abc"

    settings.BRIEVIE_PUBLIC_URLS = []
    assert public_base_url(request) == "http://testserver"


def test_new_token_returns_the_token_and_its_hash() -> None:
    token, digest = new_token()

    assert len(token) == 43
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    assert token_hash(f" {token}\n") == digest
    assert new_token()[0] != token


# --- Links point where the web interface answers (S 6, external access) -------------------

HOME = "https://192.168.1.20:443"
VPN = "https://nas.tail1234.ts.net:443"
PUBLIC = "https://brievie.example.nl"


@pytest.fixture
def three_ways(settings: Settings) -> Settings:
    settings.BRIEVIE_PUBLIC_URLS = [HOME, VPN, PUBLIC]
    settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS = [PUBLIC]
    settings.ALLOWED_HOSTS = ["192.168.1.20", "nas.tail1234.ts.net", "brievie.example.nl"]
    return settings


def test_links_use_the_public_url_when_public_web_is_on(
    rf: RequestFactory, three_ways: Settings
) -> None:
    three_ways.BRIEVIE_PUBLIC_WEB = True
    request = rf.get("/")
    assert web_urls() == [HOME, VPN, PUBLIC]
    assert public_base_url(request) == PUBLIC
    assert fragment_link(request, "/uitnodiging", "t") == f"{PUBLIC}/uitnodiging#t"


def test_links_stay_home_when_public_web_is_off(rf: RequestFactory, three_ways: Settings) -> None:
    three_ways.BRIEVIE_PUBLIC_WEB = False
    request = rf.get("/")
    assert web_urls() == [HOME, VPN]
    assert public_base_url(request) == HOME


def test_home_network_only_ignores_the_public_url_while_public_web_is_off(
    rf: RequestFactory, three_ways: Settings
) -> None:
    request = rf.get("/", HTTP_HOST="192.168.1.20")
    three_ways.BRIEVIE_PUBLIC_WEB = False
    # nas.tail1234.ts.net is no home name: through the VPN the link works away from home.
    three_ways.BRIEVIE_PUBLIC_URLS = [HOME, PUBLIC]
    assert home_network_only(request) is True
    three_ways.BRIEVIE_PUBLIC_WEB = True
    assert home_network_only(request) is False

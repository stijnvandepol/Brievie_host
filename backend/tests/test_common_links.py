import hashlib

import pytest
from django.test import RequestFactory
from pytest_django import Settings

from apps.common.links import fragment_link, home_network_only, public_base_url
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

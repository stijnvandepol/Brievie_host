import pytest
from django.http import HttpRequest
from django.test import RequestFactory
from pytest_django import Settings

from apps.common.net import client_ip, valid_client_ip


def request_from(remote_addr: str, forwarded: str | None = None) -> HttpRequest:
    if forwarded is None:
        return RequestFactory().get("/", REMOTE_ADDR=remote_addr)
    return RequestFactory().get("/", REMOTE_ADDR=remote_addr, HTTP_X_FORWARDED_FOR=forwarded)


def test_without_proxy_the_peer_address_counts(settings: Settings) -> None:
    settings.BRIEVIE_PROXY_COUNT = 0
    assert client_ip(request_from("203.0.113.5", "198.51.100.1")) == "203.0.113.5"


def test_behind_one_proxy_the_last_forwarded_entry_counts(settings: Settings) -> None:
    settings.BRIEVIE_PROXY_COUNT = 1
    # The client forged the first entry; Caddy appended the real one.
    assert client_ip(request_from("172.18.0.3", "1.2.3.4, 203.0.113.5")) == "203.0.113.5"


def test_behind_a_proxy_without_header_falls_back_to_the_peer(settings: Settings) -> None:
    settings.BRIEVIE_PROXY_COUNT = 1
    assert client_ip(request_from("172.18.0.3")) == "172.18.0.3"


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    [
        ("192.0.2.7", "192.0.2.7"),
        ("2001:db8::1", "2001:db8::1"),
        ("unknown", None),
        ("1.2.3.4:80", None),
        ("fe80::1%eth0", None),
        ("", None),
    ],
)
def test_valid_client_ip_refuses_what_inet_cannot_hold(
    settings: Settings, forwarded: str, expected: str | None
) -> None:
    settings.BRIEVIE_PROXY_COUNT = 1
    # Without a usable header the peer counts; give it a value inet cannot hold either.
    request = RequestFactory().get("/", REMOTE_ADDR="", HTTP_X_FORWARDED_FOR=forwarded)
    assert valid_client_ip(request) == expected


def test_valid_client_ip_without_proxy_is_the_peer(settings: Settings) -> None:
    settings.BRIEVIE_PROXY_COUNT = 0
    assert valid_client_ip(request_from("203.0.113.5", "unknown")) == "203.0.113.5"

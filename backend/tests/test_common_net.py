import pytest
from django.http import HttpRequest
from django.test import RequestFactory
from pytest_django import Settings

from apps.common.net import address_bucket, client_ip, valid_client_ip


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


def test_behind_caddy_a_forged_header_is_never_the_address(settings: Settings) -> None:
    # Caddy replaces a client's X-Forwarded-For with the peer it sees (no trusted
    # proxies, tests/test_deploy_caddyfile.py); were it to append instead, the forged
    # entries would come first and still not count.
    settings.BRIEVIE_PROXY_COUNT = 1
    assert client_ip(request_from("172.18.0.3", "203.0.113.5")) == "203.0.113.5"
    forged = request_from("172.18.0.3", "10.0.0.1, 127.0.0.1, 203.0.113.5")
    assert client_ip(forged) == "203.0.113.5"


def test_behind_two_proxies_the_second_entry_from_the_right_counts(settings: Settings) -> None:
    # A NAS proxy or Cloudflare Tunnel in front of Caddy: Caddy must trust exactly that
    # proxy (it then appends the proxy's address) and BRIEVIE_PROXY_COUNT must be 2.
    settings.BRIEVIE_PROXY_COUNT = 2
    request = request_from("172.18.0.3", "1.2.3.4, 203.0.113.5, 192.168.1.10")
    assert client_ip(request) == "203.0.113.5"


@pytest.mark.parametrize(
    ("address", "bucket"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("unknown", "unknown"),
        (None, "unknown"),
    ],
)
def test_limits_count_an_ipv6_client_per_64(address: str | None, bucket: str) -> None:
    # Security review 2026-11-20: one host holds a whole /64 and could rotate per attempt.
    assert address_bucket(address) == bucket

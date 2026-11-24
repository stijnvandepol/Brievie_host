import secrets

import pytest
from django.core.checks import Error, Warning, run_checks
from pytest_django import Settings

from apps.common.checks import (
    check_database_passwords,
    check_external_access,
    check_public_urls,
)


def test_public_url_with_an_allowed_host_passes(settings: Settings) -> None:
    settings.ALLOWED_HOSTS = ["192.168.1.20"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://192.168.1.20", "https://192.168.1.20:8443"]

    assert check_public_urls(None) == []


def test_public_url_host_outside_allowed_hosts_is_an_error(settings: Settings) -> None:
    settings.ALLOWED_HOSTS = ["localhost"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://localhost", "https://brievie.local:8443"]

    errors = check_public_urls(None)

    assert len(errors) == 1
    assert isinstance(errors[0], Error)
    assert errors[0].id == "brievie.E001"
    assert "brievie.local" in errors[0].msg
    assert "ALLOWED_HOSTS" in errors[0].msg
    assert errors[0].hint is not None
    assert "BRIEVIE_HOSTNAME" in errors[0].hint


def test_subdomain_patterns_and_wildcards_are_honoured(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://post.example.nl"]
    settings.ALLOWED_HOSTS = [".example.nl"]
    assert check_public_urls(None) == []
    settings.ALLOWED_HOSTS = ["*"]
    assert check_public_urls(None) == []


def test_ipv6_host_is_compared_with_its_brackets(settings: Settings) -> None:
    settings.BRIEVIE_PUBLIC_URLS = ["https://[fd00::1]:8443"]
    settings.ALLOWED_HOSTS = ["[fd00::1]"]
    assert check_public_urls(None) == []
    settings.ALLOWED_HOSTS = ["localhost"]
    assert len(check_public_urls(None)) == 1


def test_the_check_is_registered(settings: Settings) -> None:
    settings.ALLOWED_HOSTS = ["localhost"]
    settings.BRIEVIE_PUBLIC_URLS = ["https://elders.example"]

    assert "brievie.E001" in {m.id for m in run_checks()}


def test_placeholder_database_password_is_a_warning(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = False
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", "change-me-app")
    settings.DATABASE_OWNER = {"PASSWORD": "change-me-owner"}

    messages = check_database_passwords(None)

    assert [m.id for m in messages] == ["brievie.W001", "brievie.W001"]
    assert all(isinstance(m, Warning) for m in messages)
    assert all("change-me" not in (m.hint or "") for m in messages)


def test_real_database_passwords_pass(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.DEBUG = False
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", "7f3a9c")
    settings.DATABASE_OWNER = {"PASSWORD": "b41d02"}

    assert check_database_passwords(None) == []


def test_placeholder_database_password_is_fine_in_debug(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = True
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", "change-me-app")
    settings.DATABASE_OWNER = None

    assert check_database_passwords(None) == []


def test_any_password_containing_change_me_is_a_warning(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DEBUG = False
    monkeypatch.setitem(settings.DATABASES["default"], "PASSWORD", "x-change-me-too")
    settings.DATABASE_OWNER = None

    assert [m.id for m in check_database_passwords(None)] == ["brievie.W001"]


# --- External access (S 7.5, 7.6) ----------------------------------------------------------


def _external(settings: Settings, *, tunnel: bool, proxies: list[str], public_web: bool) -> None:
    settings.BRIEVIE_TUNNEL_TOKEN_SET = tunnel
    settings.BRIEVIE_TRUSTED_PROXIES = proxies
    settings.BRIEVIE_PUBLIC_WEB = public_web
    settings.BRIEVIE_SECOND_FACTOR_AVAILABLE = False
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 33554432
    settings.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl" if tunnel or proxies else ""


@pytest.mark.parametrize(("tunnel", "proxies"), [(True, []), (False, ["192.168.1.5/32"])])
def test_w002_public_web_without_second_factor(
    settings: Settings, tunnel: bool, proxies: list[str]
) -> None:
    _external(settings, tunnel=tunnel, proxies=proxies, public_web=True)
    found = check_external_access(None)
    assert [m.id for m in found] == ["brievie.W002"]
    assert isinstance(found[0], Warning)


def test_w002_silent_when_public_web_off(settings: Settings) -> None:
    _external(settings, tunnel=True, proxies=["192.168.1.5/32"], public_web=False)
    assert check_external_access(None) == []


def test_w002_silent_without_a_public_way(settings: Settings) -> None:
    _external(settings, tunnel=False, proxies=[], public_web=True)
    assert check_external_access(None) == []


def test_w003_upload_limit_above_cloudflare_with_tunnel(settings: Settings) -> None:
    _external(settings, tunnel=True, proxies=[], public_web=False)
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 100_000_001
    assert [m.id for m in check_external_access(None)] == ["brievie.W003"]
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 100_000_000
    assert check_external_access(None) == []


def test_w003_silent_without_tunnel(settings: Settings) -> None:
    _external(settings, tunnel=False, proxies=["192.168.1.5/32"], public_web=False)
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 200_000_000
    assert check_external_access(None) == []


def test_warnings_never_print_the_token(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Made at run time: no token-like literal in the repository.
    secret = secrets.token_urlsafe(32)
    monkeypatch.setenv("BRIEVIE_TUNNEL_TOKEN", secret)
    monkeypatch.setenv("TUNNEL_TOKEN", secret)
    _external(settings, tunnel=True, proxies=[], public_web=True)
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 200_000_000
    found = check_external_access(None)
    assert {m.id for m in found} == {"brievie.W002", "brievie.W003"}
    for message in found:
        assert secret not in f"{message.msg} {message.hint}"
    assert not hasattr(settings, "BRIEVIE_TUNNEL_TOKEN")


def test_w004_public_name_without_a_way(settings: Settings) -> None:
    _external(settings, tunnel=False, proxies=[], public_web=False)
    settings.BRIEVIE_PUBLIC_HOSTNAME = "brievie.example.nl"
    assert [m.id for m in check_external_access(None)] == ["brievie.W004"]
    settings.BRIEVIE_TRUSTED_PROXIES = ["192.168.1.5/32"]
    assert check_external_access(None) == []
    settings.BRIEVIE_TRUSTED_PROXIES = []
    settings.BRIEVIE_TUNNEL_TOKEN_SET = True
    assert check_external_access(None) == []

import pytest
from django.core.checks import Error, Warning, run_checks
from pytest_django import Settings

from apps.common.checks import check_database_passwords, check_public_urls


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

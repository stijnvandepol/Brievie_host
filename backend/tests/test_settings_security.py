import runpy
from pathlib import Path

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured

import brievie.settings

SECURITY_ENV = (
    "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE",
    "SECURE_HSTS_SECONDS",
    "CSRF_TRUSTED_ORIGINS",
    "BRIEVIE_DB_ROLE",
    "DATABASE_OWNER_URL",
)


@pytest.fixture
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Evaluate settings.py again with none of the overridable variables set."""
    for name in SECURITY_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    return runpy.run_path(str(Path(brievie.settings.__file__)))


def test_secure_defaults(fresh_settings: dict[str, object]) -> None:
    assert fresh_settings["SESSION_COOKIE_SECURE"] is True
    assert fresh_settings["CSRF_COOKIE_SECURE"] is True
    assert fresh_settings["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert fresh_settings["SECURE_PROXY_SSL_HEADER"] == ("HTTP_X_FORWARDED_PROTO", "https")
    assert fresh_settings["SECURE_HSTS_SECONDS"] == 31536000
    assert fresh_settings["CSRF_TRUSTED_ORIGINS"] == []


def test_serving_process_needs_no_owner_credentials(fresh_settings: dict[str, object]) -> None:
    assert fresh_settings["DATABASE_OWNER"] is None


def test_owner_role_requires_the_owner_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_OWNER_URL", raising=False)
    monkeypatch.setenv("BRIEVIE_DB_ROLE", "owner")
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    with pytest.raises(ImproperlyConfigured, match="DATABASE_OWNER_URL"):
        runpy.run_path(str(Path(brievie.settings.__file__)))


def test_only_dutch_is_offered(fresh_settings: dict[str, object]) -> None:
    assert fresh_settings["LANGUAGES"] == [("nl", "Nederlands")]

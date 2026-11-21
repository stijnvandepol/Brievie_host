import runpy
from pathlib import Path

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured

import brievie.settings

SECURITY_ENV = (
    "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE",
    "BRIEVIE_HSTS_SECONDS",
    "BRIEVIE_SETUP_TOKEN",
    "BRIEVIE_MAX_BODY_BYTES",
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


def test_dutch_and_english_are_offered_dutch_first(fresh_settings: dict[str, object]) -> None:
    assert fresh_settings["LANGUAGES"] == [("nl", "Nederlands"), ("en", "English")]
    assert fresh_settings["LANGUAGE_CODE"] == "nl"


def test_public_urls_must_be_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_PUBLIC_URLS", "https://brievie.local,http://brievie.example.nl")
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    with pytest.raises(ImproperlyConfigured, match="https://"):
        runpy.run_path(str(Path(brievie.settings.__file__)))


def test_public_urls_keep_their_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_PUBLIC_URLS", "https://brievie.local, https://brievie.example.nl")
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    values = runpy.run_path(str(Path(brievie.settings.__file__)))
    assert values["BRIEVIE_PUBLIC_URLS"] == ["https://brievie.local", "https://brievie.example.nl"]


def test_hsts_follows_brievie_hsts_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_HSTS_SECONDS", "0")
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    values = runpy.run_path(str(Path(brievie.settings.__file__)))
    assert values["SECURE_HSTS_SECONDS"] == 0


def test_setup_token_from_env_must_be_long_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_SETUP_TOKEN", "te-kort-123")
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    with pytest.raises(ImproperlyConfigured, match="at least 16"):
        runpy.run_path(str(Path(brievie.settings.__file__)))


def test_setup_token_of_16_characters_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_SETUP_TOKEN", "x" * 16)
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    assert runpy.run_path(str(Path(brievie.settings.__file__)))["BRIEVIE_SETUP_TOKEN"] == "x" * 16


def test_body_limit_defaults_to_2_5_mib(fresh_settings: dict[str, object]) -> None:
    assert fresh_settings["DATA_UPLOAD_MAX_MEMORY_SIZE"] == 2621440


def test_body_limit_follows_brievie_max_body_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_MAX_BODY_BYTES", "1048576")
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    values = runpy.run_path(str(Path(brievie.settings.__file__)))
    assert values["DATA_UPLOAD_MAX_MEMORY_SIZE"] == 1048576


GOOD_SECRET = "k" * 50


def run_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> dict[str, object]:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)
    return runpy.run_path(str(Path(brievie.settings.__file__)))


@pytest.mark.parametrize(
    "secret",
    ["change-me-to-a-long-random-string" + "x" * 30, "k" * 49, ""],
    ids=["placeholder", "too-short", "empty"],
)
def test_placeholder_or_short_secret_key_is_refused(
    monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        run_settings(monkeypatch, DEBUG="false", SECRET_KEY=secret)


def test_long_random_secret_key_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    values = run_settings(monkeypatch, DEBUG="false", SECRET_KEY=GOOD_SECRET)
    assert values["SECRET_KEY"] == GOOD_SECRET


def test_debug_accepts_a_short_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    values = run_settings(monkeypatch, DEBUG="true", SECRET_KEY="change-me")
    assert values["SECRET_KEY"] == "change-me"

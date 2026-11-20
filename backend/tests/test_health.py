import logging

import pytest
from django.db import OperationalError, connection
from django.http import HttpResponse
from django.test import Client
from django.urls import URLPattern, URLResolver, path, resolve

from apps.common import health
from apps.common.version import server_version


def test_health_returns_status_and_versions(client: Client) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "api_version": "1.0.0", "server_version": server_version()}


def test_health_is_503_when_the_database_is_unreachable(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("apps.common.health.database_reachable", lambda: False)
    r = client.get("/api/v1/health")
    assert r.status_code == 503
    assert r.json()["code"] == "database_unavailable"
    assert set(r.json()) == {"code", "message"}
    assert "database" in r.json()["message"]


class _BrokenCursor:
    def __enter__(self) -> "_BrokenCursor":
        raise OperationalError('connection to server at "db.secret.example" failed')

    def __exit__(self, *args: object) -> None:
        return None


class _BrokenConnection:
    def cursor(self) -> _BrokenCursor:
        return _BrokenCursor()


def test_database_reachable_is_false_on_a_database_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Only the health module's own name, and only around the call: the test database's
    # set-up and teardown keep the real connection.
    with (
        monkeypatch.context() as patch,
        caplog.at_level(logging.WARNING, logger="apps.common.health"),
    ):
        patch.setattr(health, "connection", _BrokenConnection())
        assert health.database_reachable() is False
    assert vars(health)["connection"] is connection
    assert "OperationalError" in caplog.text
    assert "db.secret" not in caplog.text


def test_health_skips_the_request_transaction() -> None:
    assert resolve("/api/v1/health").func._non_atomic_requests == {"default"}  # type: ignore[attr-defined]
    assert not hasattr(resolve("/api/v1/workspaces").func, "_non_atomic_requests")


def test_mark_non_atomic_refuses_an_unknown_route() -> None:
    patterns: list[URLPattern | URLResolver] = [path("health", lambda request: HttpResponse())]
    with pytest.raises(LookupError):
        health.mark_non_atomic((patterns, "ninja", "api-v1"), "no-such-route")


def test_unknown_api_path_uses_error_shape(client: Client) -> None:
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert set(r.json()) == {"code", "message"}

from django.test import Client


def test_health_returns_status_and_api_version(client: Client) -> None:
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "api_version": "1.0.0"}


def test_unknown_api_path_uses_error_shape(client: Client) -> None:
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert set(r.json()) == {"code", "message"}

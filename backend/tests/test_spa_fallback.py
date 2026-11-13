"""The web app is served for every non-API path; /api keeps its JSON answers."""

from pathlib import Path

import pytest
from django.test import Client
from pytest_django import Settings

INDEX = "<!doctype html><title>Brievie</title><div id=root></div>"


@pytest.fixture(autouse=True)
def web_dist(tmp_path: Path, settings: Settings) -> Path:
    (tmp_path / "index.html").write_text(INDEX)
    settings.BRIEVIE_WEB_DIST = tmp_path
    return tmp_path


@pytest.mark.parametrize("path", ["/", "/koppel-app", "/login", "/setup", "/een/diepe/route"])
def test_client_routes_get_the_index(client: Client, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert r["Content-Type"].startswith("text/html")
    assert r.content.decode() == INDEX
    # The shell is never cached, so a new build is picked up at once.
    assert r["Cache-Control"] == "no-cache"


def test_index_sets_the_csrf_cookie_for_the_app(client: Client) -> None:
    r = client.get("/koppel-app")
    assert r.cookies["csrftoken"].value


def test_unknown_api_path_still_returns_the_json_404(client: Client) -> None:
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    assert r["Content-Type"] == "application/json"
    assert r.json() == {"code": "not_found", "message": "We kunnen dit niet vinden."}


@pytest.mark.parametrize("path", ["/api", "/api/", "/api/other"])
def test_api_prefix_is_never_the_index(client: Client, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 404
    assert r["Content-Type"] == "application/json"


@pytest.mark.parametrize("path", ["/assets/missing-1a2b3c4d.js", "/robots.txt", "/x/y.png"])
def test_missing_file_is_a_404_and_not_the_index(client: Client, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 404
    assert INDEX not in r.content.decode()


def test_only_safe_methods(client: Client) -> None:
    assert client.post("/koppel-app").status_code == 405
    assert client.head("/koppel-app").status_code == 200


def test_missing_build_is_a_404(client: Client, web_dist: Path) -> None:
    (web_dist / "index.html").unlink()
    assert client.get("/").status_code == 404

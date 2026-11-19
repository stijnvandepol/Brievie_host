"""Response headers that the DAST scan checks: caching of API answers, the error shape of
unknown /api paths whatever the method, and no CORS grant on the web app's files."""

from pathlib import Path

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import Client, RequestFactory, override_settings

from apps.common.errors import ApiCacheControlMiddleware


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/setup", "/api/v1/nope"])
def test_api_answers_are_never_stored(client: Client, path: str) -> None:
    # An API answer can hold personal data: no browser or proxy keeps a copy.
    r = client.get(path)
    assert r["Cache-Control"] == "no-store"


def test_an_endpoints_own_cache_control_is_kept() -> None:
    def own(request: HttpRequest) -> HttpResponse:
        response = HttpResponse(b"{}", content_type="application/json")
        response["Cache-Control"] = "private, no-store"
        return response

    request = RequestFactory().get("/api/v1/anything")
    assert ApiCacheControlMiddleware(own)(request)["Cache-Control"] == "private, no-store"


def test_web_app_paths_keep_their_own_caching() -> None:
    def page(request: HttpRequest) -> HttpResponse:
        return HttpResponse(b"<!doctype html>", content_type="text/html")

    request = RequestFactory().get("/inbox")
    assert not ApiCacheControlMiddleware(page)(request).has_header("Cache-Control")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api"),
        ("post", "/api/v1/devices/abc/"),
        ("put", "/api/v1/workspaces/abc/labels/def/"),
        ("delete", "/api/v1/me/sessions/abc/"),
    ],
)
def test_unknown_api_paths_answer_the_json_404_for_every_method(method: str, path: str) -> None:
    # With CSRF enforced, as in production: never Django's HTML CSRF page.
    client = Client(enforce_csrf_checks=True)
    r = getattr(client, method)(path, "{}", content_type="application/json")
    assert r.status_code == 404
    assert r["Content-Type"] == "application/json"
    assert r.json() == {"code": "not_found", "message": "We kunnen dit niet vinden."}


def test_the_web_apps_files_grant_no_cross_origin_access(tmp_path: Path) -> None:
    # WhiteNoise sends `Access-Control-Allow-Origin: *` by default; the web app loads its
    # files from its own origin only, so no other site needs to read them.
    (tmp_path / "favicon.svg").write_text("<svg/>")
    with override_settings(WHITENOISE_ROOT=tmp_path):
        # A new client builds its middleware now, so WhiteNoise sees this root.
        response = Client().get("/favicon.svg")
    assert response.status_code == 200
    assert not response.has_header("Access-Control-Allow-Origin")

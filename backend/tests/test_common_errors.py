from django.http import Http404, HttpRequest
from ninja import NinjaAPI
from ninja.testing import TestClient

from apps.common.errors import ApiError, install_error_handlers

errors_api = NinjaAPI(urls_namespace="test-errors")
install_error_handlers(errors_api)


@errors_api.get("/conflict", auth=None)
def conflict(request: HttpRequest) -> None:
    raise ApiError(409, "already_set_up", "Brievie is al ingesteld.")


@errors_api.get("/number", auth=None)
def number(request: HttpRequest, n: int) -> int:
    return n


@errors_api.get("/missing", auth=None)
def missing(request: HttpRequest) -> None:
    raise Http404


client = TestClient(errors_api)


def test_api_error_renders_its_status_code_and_message() -> None:
    r = client.get("/conflict")
    assert r.status_code == 409
    assert r.json() == {"code": "already_set_up", "message": "Brievie is al ingesteld."}


def test_validation_error_is_422_with_error_shape() -> None:
    r = client.get("/number?n=abc")
    assert r.status_code == 422
    body = r.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == "validation_error"


def test_http404_inside_an_endpoint_uses_error_shape() -> None:
    r = client.get("/missing")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
    assert set(r.json()) == {"code", "message"}

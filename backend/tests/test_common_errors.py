import logging

import pytest
from django.test import Client

from apps.workspaces.models import Workspace

pytestmark = pytest.mark.urls("tests.error_api")


def assert_error(r_json: object, code: str) -> None:
    assert isinstance(r_json, dict)
    assert set(r_json) == {"code", "message"}
    assert r_json["code"] == code
    assert r_json["message"]


def test_api_error_renders_its_status_code_and_message(client: Client) -> None:
    r = client.get("/api/conflict")
    assert r.status_code == 409
    assert r.json() == {"code": "already_set_up", "message": "Brievie is al ingesteld."}


def test_validation_error_is_422_with_error_shape(client: Client) -> None:
    r = client.get("/api/number?n=abc")
    assert r.status_code == 422
    assert_error(r.json(), "validation_error")


def test_http404_inside_an_endpoint_uses_error_shape(client: Client) -> None:
    r = client.get("/api/missing")
    assert r.status_code == 404
    assert_error(r.json(), "not_found")


def test_authentication_error_is_401_not_authenticated(client: Client) -> None:
    r = client.get("/api/unauthenticated")
    assert r.status_code == 401
    assert_error(r.json(), "not_authenticated")


def test_authorization_error_is_403_forbidden(client: Client) -> None:
    r = client.get("/api/forbidden")
    assert r.status_code == 403
    assert_error(r.json(), "forbidden")


def test_throttled_is_429_too_many_attempts_with_retry_after(client: Client) -> None:
    r = client.get("/api/throttled")
    assert r.status_code == 429
    assert_error(r.json(), "too_many_attempts")
    assert r.headers["Retry-After"] == "30"


def test_other_http_errors_keep_their_status_with_error_shape(client: Client) -> None:
    r = client.get("/api/teapot")
    assert r.status_code == 418
    assert_error(r.json(), "error")


def test_wrong_method_is_405_method_not_allowed(client: Client) -> None:
    r = client.delete("/api/conflict")
    assert r.status_code == 405
    assert_error(r.json(), "method_not_allowed")
    assert r.headers["Allow"] == "GET"


@pytest.mark.urls("brievie.urls")
def test_wrong_method_on_the_real_api_is_405_with_error_shape(client: Client) -> None:
    r = client.post("/api/v1/health", content_type="application/json")
    assert r.status_code == 405
    assert_error(r.json(), "method_not_allowed")


def test_unhandled_exception_is_500_internal_error_and_rolls_back(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        r = client.post(
            "/api/crash", data={"secret": "geheim-123"}, content_type="application/json"
        )
    assert r.status_code == 500
    assert_error(r.json(), "internal_error")
    assert Workspace.objects.count() == 0
    assert "boom" in caplog.text
    assert "geheim-123" not in caplog.text


def assert_no_error_logs(caplog: pytest.LogCaptureFixture) -> None:
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


@pytest.mark.parametrize("url", ["/api/suspicious", "/api/bad-request"])
def test_suspicious_operation_is_400_bad_request_without_error_log(
    client: Client, caplog: pytest.LogCaptureFixture, url: str
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = client.get(url)
    assert r.status_code == 400
    assert_error(r.json(), "bad_request")
    assert_no_error_logs(caplog)


def test_request_data_too_big_is_413_without_error_log(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = client.get("/api/too-big")
    assert r.status_code == 413
    assert_error(r.json(), "payload_too_large")
    assert_no_error_logs(caplog)


def test_permission_denied_is_403_forbidden_without_error_log(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = client.get("/api/permission-denied")
    assert r.status_code == 403
    assert_error(r.json(), "forbidden")
    assert_no_error_logs(caplog)


def test_model_validation_error_is_422_with_its_first_message(
    client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        r = client.get("/api/model-validation")
    assert r.status_code == 422
    assert r.json() == {"code": "validation_error", "message": "Een map heeft een naam nodig."}
    assert_no_error_logs(caplog)

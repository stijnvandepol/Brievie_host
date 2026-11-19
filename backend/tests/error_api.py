"""A throwaway API, mounted only by tests/test_common_errors.py, that fails on purpose."""

from django.contrib.sessions.exceptions import SessionInterrupted
from django.core.exceptions import (
    BadRequest,
    PermissionDenied,
    RequestDataTooBig,
    SuspiciousOperation,
    ValidationError,
)
from django.db import OperationalError
from django.http import Http404, HttpRequest
from django.urls import path
from ninja import NinjaAPI, Schema
from ninja.errors import AuthenticationError, AuthorizationError, HttpError, Throttled
from psycopg import errors as pg_errors

from apps.common.errors import ApiError, install_error_handlers
from apps.workspaces.models import Workspace

errors_api = NinjaAPI(urls_namespace="test-errors")
install_error_handlers(errors_api)


class SecretIn(Schema):
    secret: str


@errors_api.get("/conflict", auth=None)
def conflict(request: HttpRequest) -> None:
    raise ApiError(409, "already_set_up", "Brievie is al ingesteld.")


@errors_api.get("/number", auth=None)
def number(request: HttpRequest, n: int) -> int:
    return n


@errors_api.get("/missing", auth=None)
def missing(request: HttpRequest) -> None:
    raise Http404


@errors_api.get("/unauthenticated", auth=None)
def unauthenticated(request: HttpRequest) -> None:
    raise AuthenticationError


@errors_api.get("/forbidden", auth=None)
def forbidden(request: HttpRequest) -> None:
    raise AuthorizationError


@errors_api.get("/throttled", auth=None)
def throttled(request: HttpRequest) -> None:
    raise Throttled(wait=30)


@errors_api.get("/teapot", auth=None)
def teapot(request: HttpRequest) -> None:
    raise HttpError(418, "I'm a teapot")


@errors_api.post("/crash", auth=None)
def crash(request: HttpRequest, payload: SecretIn) -> None:
    Workspace.objects.create(name="Half af", kind="home")
    raise RuntimeError("boom")


@errors_api.get("/suspicious", auth=None)
def suspicious(request: HttpRequest) -> None:
    raise SuspiciousOperation("Invalid HTTP_HOST header")


@errors_api.get("/session-interrupted", auth=None)
def session_interrupted(request: HttpRequest) -> None:
    raise SessionInterrupted("The session was deleted before the request completed.")


@errors_api.get("/bad-request", auth=None)
def bad_request(request: HttpRequest) -> None:
    raise BadRequest("Malformed")


@errors_api.get("/too-big", auth=None)
def too_big(request: HttpRequest) -> None:
    raise RequestDataTooBig("Request body exceeded settings.DATA_UPLOAD_MAX_MEMORY_SIZE.")


@errors_api.get("/permission-denied", auth=None)
def permission_denied(request: HttpRequest) -> None:
    raise PermissionDenied


@errors_api.get("/model-validation", auth=None)
def model_validation(request: HttpRequest) -> None:
    raise ValidationError({"name": ValidationError("Een map heeft een naam nodig.", code="blank")})


def _database_failure(cause: Exception) -> None:
    Workspace.objects.create(name="Half af", kind="home")
    try:
        raise cause
    except Exception as exc:
        raise OperationalError(str(exc)) from exc


@errors_api.get("/deadlock", auth=None)
def deadlock(request: HttpRequest) -> None:
    _database_failure(pg_errors.DeadlockDetected("deadlock detected"))


@errors_api.get("/serialization", auth=None)
def serialization(request: HttpRequest) -> None:
    _database_failure(pg_errors.SerializationFailure("could not serialize access"))


@errors_api.get("/database-down", auth=None)
def database_down(request: HttpRequest) -> None:
    _database_failure(RuntimeError("connection lost"))


urlpatterns = [path("api/", errors_api.urls)]

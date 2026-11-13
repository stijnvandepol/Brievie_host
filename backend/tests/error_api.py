"""A throwaway API, mounted only by tests/test_common_errors.py, that fails on purpose."""

from django.http import Http404, HttpRequest
from django.urls import path
from ninja import NinjaAPI, Schema
from ninja.errors import AuthenticationError, AuthorizationError, HttpError, Throttled

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


urlpatterns = [path("api/", errors_api.urls)]

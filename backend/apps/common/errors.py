import logging
from collections.abc import Callable

from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.utils.translation import gettext as _
from ninja import NinjaAPI
from ninja.errors import HttpError, Throttled, ValidationError

from apps.common.schemas import ErrorOut

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """An expected failure, rendered as `{"code", "message"}` with the given status.

    It does not roll back the request transaction: raise it before writing, or
    keep writes that must survive the error (such as a failed-login record).
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JsonResponse:
    return JsonResponse(ErrorOut(code=code, message=message).model_dump(), status=status)


def _http_error_body(status: int) -> tuple[str, str]:
    """Code and Dutch message for a status raised by Ninja itself (auth, throttling, ...)."""
    bodies = {
        400: ("bad_request", _("Dit verzoek klopt niet.")),
        401: ("not_authenticated", _("Je bent niet ingelogd.")),
        403: ("forbidden", _("Je mag dit niet doen.")),
        404: ("not_found", _("We kunnen dit niet vinden.")),
        405: ("method_not_allowed", _("Dit kan hier niet.")),
        429: ("too_many_attempts", _("Te veel pogingen. Probeer het later opnieuw.")),
    }
    return bodies.get(status, ("error", _("Er ging iets mis.")))


def http_error_response(status: int) -> JsonResponse:
    code, message = _http_error_body(status)
    return error_response(status, code, message)


def install_error_handlers(api: NinjaAPI) -> None:
    def handle_api_error(request: HttpRequest, exc: ApiError) -> JsonResponse:
        return error_response(exc.status, exc.code, exc.message)

    def handle_validation_error(request: HttpRequest, exc: object) -> JsonResponse:
        message = _("De gegevens kloppen niet. Controleer wat je hebt ingevuld.")
        return error_response(422, "validation_error", message)

    def handle_not_found(request: HttpRequest, exc: object) -> JsonResponse:
        return http_error_response(404)

    def handle_http_error(request: HttpRequest, exc: HttpError) -> JsonResponse:
        response = http_error_response(exc.status_code)
        if isinstance(exc, Throttled) and exc.wait is not None:
            response["Retry-After"] = str(exc.wait)
        return response

    def handle_unexpected_error(request: HttpRequest, exc: object) -> JsonResponse:
        # The view returns normally, so ATOMIC_REQUESTS would commit half-done work.
        transaction.set_rollback(True)
        # Method and path only: the body may hold document content or passwords.
        logger.error(
            "Unhandled error in %s %s",
            request.method,
            request.path,
            exc_info=exc if isinstance(exc, BaseException) else None,
        )
        message = _("Er ging iets mis op de server. Probeer het later opnieuw.")
        return error_response(500, "internal_error", message)

    # Ninja types the handler argument as "instance or class"; it always passes an instance.
    api.add_exception_handler(ApiError, handle_api_error)  # type: ignore[arg-type]
    api.add_exception_handler(HttpError, handle_http_error)  # type: ignore[arg-type]
    api.add_exception_handler(ValidationError, handle_validation_error)
    api.add_exception_handler(Http404, handle_not_found)
    api.add_exception_handler(Exception, handle_unexpected_error)


def api_path_not_found(request: HttpRequest) -> JsonResponse:
    """Fallback for /api/ paths that no endpoint matches."""
    return http_error_response(404)


class ApiMethodNotAllowedMiddleware:
    """Gives Ninja's bare 405 (wrong HTTP method) the standard error body."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if response.status_code == 405 and request.path.startswith("/api/"):
            body = http_error_response(405)
            if response.has_header("Allow"):
                body["Allow"] = response["Allow"]
            return body
        return response

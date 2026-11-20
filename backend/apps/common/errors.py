import logging
import re
from collections.abc import Callable

from django.conf import settings
from django.contrib.sessions.exceptions import SessionInterrupted
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import (
    BadRequest,
    PermissionDenied,
    RequestDataTooBig,
    SuspiciousOperation,
)
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import OperationalError, transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from ninja import NinjaAPI
from ninja.errors import HttpError, Throttled, ValidationError
from psycopg import errors as pg_errors

from apps.common.input_errors import generic, message_for
from apps.common.schemas import ErrorOut

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """An expected failure, rendered as `{"code", "message"}` with the given status.

    Like Ninja's `HttpError` family and `Http404`, it does not roll back the
    request transaction: the view returns a response, so ATOMIC_REQUESTS commits
    earlier writes (which keeps, for example, a failed-login record). Project rule:
    every mutating service function is wrapped in `transaction.atomic`, so raising
    inside it rolls back its own writes.
    """

    def __init__(
        self, status: int, code: str, message: str, *, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}


def is_try_again(exc: BaseException) -> bool:
    """A PostgreSQL deadlock or serialization failure: two changes met, and trying again
    works. The API answers it with 409 `try_again`."""
    return isinstance(exc, OperationalError) and isinstance(
        exc.__cause__, pg_errors.DeadlockDetected | pg_errors.SerializationFailure
    )


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
        411: ("length_required", _("Dit verzoek mist een lengte. Probeer het opnieuw.")),
        413: ("payload_too_large", _("Dit verzoek is te groot.")),
        415: ("unsupported_media_type", _("Stuur de gegevens als JSON.")),
        429: ("too_many_attempts", _("Te veel pogingen. Probeer het later opnieuw.")),
    }
    return bodies.get(status, ("error", _("Er ging iets mis.")))


def http_error_response(status: int) -> JsonResponse:
    code, message = _http_error_body(status)
    return error_response(status, code, message)


def install_error_handlers(api: NinjaAPI) -> None:
    def handle_api_error(request: HttpRequest, exc: ApiError) -> JsonResponse:
        response = error_response(exc.status, exc.code, exc.message)
        for name, value in exc.headers.items():
            response[name] = value
        return response

    def handle_validation_error(request: HttpRequest, exc: ValidationError) -> JsonResponse:
        return error_response(422, "validation_error", message_for(exc.errors))

    def handle_not_found(request: HttpRequest, exc: object) -> JsonResponse:
        return http_error_response(404)

    def handle_http_error(request: HttpRequest, exc: HttpError) -> JsonResponse:
        response = http_error_response(exc.status_code)
        if isinstance(exc, Throttled) and exc.wait is not None:
            response["Retry-After"] = str(exc.wait)
        return response

    def handle_suspicious_operation(request: HttpRequest, exc: object) -> JsonResponse:
        # A client error, not a server bug: no traceback, no rollback needed.
        logger.warning("Rejected %s %s: %s", request.method, request.path, type(exc).__name__)
        return http_error_response(400)

    def handle_request_data_too_big(request: HttpRequest, exc: object) -> JsonResponse:
        logger.warning("Rejected %s %s: request body too big", request.method, request.path)
        return http_error_response(413)

    def handle_session_interrupted(request: HttpRequest, exc: object) -> JsonResponse:
        # The session was ended (another tab, "end this session") while this request ran.
        return http_error_response(401)

    def handle_permission_denied(request: HttpRequest, exc: object) -> JsonResponse:
        return http_error_response(403)

    def handle_model_validation_error(
        request: HttpRequest, exc: DjangoValidationError
    ) -> JsonResponse:
        messages = exc.messages
        message = messages[0] if messages else generic()
        return error_response(422, "validation_error", message)

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

    def handle_operational_error(request: HttpRequest, exc: OperationalError) -> JsonResponse:
        # A deadlock or serialization failure: two changes met (for example deleting a
        # label while a rule runs). PostgreSQL aborted this one; trying again works.
        if not is_try_again(exc):
            return handle_unexpected_error(request, exc)
        transaction.set_rollback(True)
        logger.warning(
            "Conflict in %s %s: %s", request.method, request.path, type(exc.__cause__).__name__
        )
        message = _("Dit lukte niet door een andere wijziging. Probeer het opnieuw.")
        return error_response(409, "try_again", message)

    # Ninja types the handler argument as "instance or class"; it always passes an instance.
    api.add_exception_handler(ApiError, handle_api_error)  # type: ignore[arg-type]
    api.add_exception_handler(HttpError, handle_http_error)  # type: ignore[arg-type]
    api.add_exception_handler(ValidationError, handle_validation_error)  # type: ignore[arg-type]
    api.add_exception_handler(Http404, handle_not_found)
    api.add_exception_handler(SuspiciousOperation, handle_suspicious_operation)
    api.add_exception_handler(BadRequest, handle_suspicious_operation)
    # A BadRequest subclass; Ninja picks the most specific handler.
    api.add_exception_handler(SessionInterrupted, handle_session_interrupted)
    # A SuspiciousOperation subclass; Ninja picks the most specific handler.
    api.add_exception_handler(RequestDataTooBig, handle_request_data_too_big)
    api.add_exception_handler(PermissionDenied, handle_permission_denied)
    api.add_exception_handler(DjangoValidationError, handle_model_validation_error)  # type: ignore[arg-type]
    api.add_exception_handler(OperationalError, handle_operational_error)  # type: ignore[arg-type]
    api.add_exception_handler(Exception, handle_unexpected_error)


@csrf_exempt  # it writes nothing: a POST is the JSON 404, not Django's HTML CSRF page
def api_path_not_found(request: HttpRequest) -> JsonResponse:
    """Fallback for /api/ paths that no endpoint matches, whatever the method."""
    return http_error_response(404)


class ApiCacheControlMiddleware:
    """`Cache-Control: no-store` on every /api/ answer that sets no caching of its own.

    An API answer can hold personal data (names, letters, sessions); no browser or
    proxy may keep a copy. An endpoint that sets its own header (a page image's
    `private, no-store`) keeps it. Web app paths keep theirs (the index: `no-cache`).
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if request.path.startswith("/api/") and not response.has_header("Cache-Control"):
            response["Cache-Control"] = "no-store"
        return response


class ApiSessionMiddleware(SessionMiddleware):
    """Django's `SessionMiddleware`, except that an /api/ request whose session was ended
    while it ran answers 401 `not_authenticated` in the error shape instead of Django's
    HTML 400 (`SessionInterrupted`), and drops the cookie. Other paths keep Django's answer.
    """

    def process_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        try:
            return super().process_response(request, response)
        except SessionInterrupted:
            if not request.path.startswith("/api/"):
                raise
            answer = http_error_response(401)
            answer.delete_cookie(
                settings.SESSION_COOKIE_NAME,
                path=settings.SESSION_COOKIE_PATH,
                domain=settings.SESSION_COOKIE_DOMAIN,
            )
            return answer


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


# The one endpoint that takes a multipart body (page images) and has a larger limit.
_DOCUMENT_UPLOAD_PATH = re.compile(r"^/api/v1/workspaces/[^/]+/documents$")


def is_document_upload(request: HttpRequest) -> bool:
    return request.method == "POST" and bool(_DOCUMENT_UPLOAD_PATH.match(request.path))


class ApiContentLengthMiddleware:
    """Refuses an /api/ request body without a Content-Length (411) or above the limit (413).

    gunicorn hands Django a chunked body without a length, and Django then reads it
    as empty, so the caller would get a misleading 415 or 422. The size check runs
    here, before authentication, so an oversized body is never read.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not request.path.startswith("/api/"):
            return self.get_response(request)
        length = request.META.get("CONTENT_LENGTH")
        if "HTTP_TRANSFER_ENCODING" in request.META and not length:
            return http_error_response(411)
        limit = (
            settings.BRIEVIE_MAX_UPLOAD_BYTES
            if is_document_upload(request)
            else settings.BRIEVIE_MAX_BODY_BYTES
        )
        try:
            too_big = int(length or 0) > limit
        except ValueError:
            return http_error_response(400)
        if too_big:
            return http_error_response(413)
        return self.get_response(request)


class ApiJsonBodyMiddleware:
    """Refuses an /api/ request body that is not `application/json` (415).

    Ninja parses the body as JSON whatever the Content-Type says, and its views are
    exempt from Django's CSRF middleware, so without this check a cross-site
    `<form enctype="text/plain">` could post JSON to the unauthenticated endpoints.
    A JSON Content-Type cannot be sent cross-site without a CORS preflight, and the
    API allows no cross-origin requests. Runs before any view, so a refused request
    changes nothing.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if (
            request.path.startswith("/api/")
            and _has_body(request)
            and request.content_type != _expected_content_type(request)
        ):
            if is_document_upload(request):
                message = _("Stuur de brief als formulier met bestanden (multipart/form-data).")
                return error_response(415, "unsupported_media_type", message)
            return http_error_response(415)
        return self.get_response(request)


def _expected_content_type(request: HttpRequest) -> str:
    # A multipart body cannot be sent cross-site to this endpoint either: it needs a
    # device token, or a session with a CSRF token.
    return "multipart/form-data" if is_document_upload(request) else "application/json"


def _has_body(request: HttpRequest) -> bool:
    if "HTTP_TRANSFER_ENCODING" in request.META:
        return True
    try:
        return int(request.META.get("CONTENT_LENGTH") or 0) > 0
    except ValueError:
        return True

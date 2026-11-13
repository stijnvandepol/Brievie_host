from django.http import Http404, HttpRequest, JsonResponse
from django.utils.translation import gettext as _
from ninja import NinjaAPI
from ninja.errors import ValidationError

from apps.common.schemas import ErrorOut


class ApiError(Exception):
    """An expected failure, rendered as `{"code", "message"}` with the given status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JsonResponse:
    return JsonResponse(ErrorOut(code=code, message=message).model_dump(), status=status)


def not_found_message() -> str:
    return _("We kunnen dit niet vinden.")


def install_error_handlers(api: NinjaAPI) -> None:
    def handle_api_error(request: HttpRequest, exc: ApiError) -> JsonResponse:
        return error_response(exc.status, exc.code, exc.message)

    def handle_validation_error(request: HttpRequest, exc: object) -> JsonResponse:
        message = _("De gegevens kloppen niet. Controleer wat je hebt ingevuld.")
        return error_response(422, "validation_error", message)

    def handle_not_found(request: HttpRequest, exc: object) -> JsonResponse:
        return error_response(404, "not_found", not_found_message())

    # Ninja types the handler argument as "instance or class"; it always passes an instance.
    api.add_exception_handler(ApiError, handle_api_error)  # type: ignore[arg-type]
    api.add_exception_handler(ValidationError, handle_validation_error)
    api.add_exception_handler(Http404, handle_not_found)


def api_path_not_found(request: HttpRequest) -> JsonResponse:
    """Fallback for /api/ paths that no endpoint matches."""
    return error_response(404, "not_found", not_found_message())

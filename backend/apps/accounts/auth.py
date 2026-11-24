from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja.errors import HttpError
from ninja.security import SessionAuth as NinjaSessionAuth

from apps.access.middleware import session_allowed
from apps.common.errors import ApiError


class SessionAuth(NinjaSessionAuth):
    """The web session, with CSRF checked only for a caller who has a session.

    Ninja's version checks CSRF before it looks at the session, so a request with no
    credentials at all answers 403. Here that answers 401 `not_authenticated`; a
    logged-in session without a valid CSRF token is still 403, with its own code
    `csrf_failed` (the client refreshes the page; a role that may not do something
    answers `forbidden`). (The class keeps Ninja's name, so the OpenAPI security scheme
    does not change.)
    """

    def _get_key(self, request: HttpRequest) -> str | None:
        if not request.user.is_authenticated or not session_allowed(request):
            return None
        try:
            return super()._get_key(request)
        except HttpError as exc:
            # Ninja's CSRF check is the only 403 this raises.
            if exc.status_code != 403:
                raise
            raise ApiError(
                403,
                "csrf_failed",
                _(
                    "Je sessie is niet meer geldig voor deze pagina. Ververs de pagina en "
                    "probeer het opnieuw."
                ),
            ) from exc


session_auth = SessionAuth()

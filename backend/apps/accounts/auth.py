from django.http import HttpRequest
from ninja.security import SessionAuth as NinjaSessionAuth


class SessionAuth(NinjaSessionAuth):
    """The web session, with CSRF checked only for a caller who has a session.

    Ninja's version checks CSRF before it looks at the session, so a request with no
    credentials at all answers 403. Here that answers 401 `not_authenticated`; a
    logged-in session without a valid CSRF token is still 403. (The class keeps
    Ninja's name, so the OpenAPI security scheme does not change.)
    """

    def _get_key(self, request: HttpRequest) -> str | None:
        if not request.user.is_authenticated:
            return None
        return super()._get_key(request)


session_auth = SessionAuth()

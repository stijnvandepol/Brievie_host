"""Authentication for the set-up of a second factor during a pending sign-in.

A sign-in that a rule stopped at the step `setup` has no session yet: only the set-up
operations accept its pending state (`getMySecondFactor`, `beginSecondFactorSetup`,
`confirmSecondFactorSetup`). Every other endpoint keeps `session_auth`, so the pending
state reaches nothing else.

No CSRF token: the browser is not signed in, and a cross-site form cannot send the JSON
these operations take (`ApiJsonBodyMiddleware`).

Never on the public route: a pending step there would be a way in by password without a
confirmed factor (security review of PR #56, point 3).
"""

from django.conf import settings
from django.http import HttpRequest
from ninja.security import APIKeyCookie

from apps.access.route import is_public_route
from apps.accounts.models import User
from apps.accounts.signin import pending_user


class PendingSetupAuth(APIKeyCookie):
    param_name = settings.SESSION_COOKIE_NAME

    def __init__(self) -> None:
        super().__init__(csrf=False)

    def authenticate(self, request: HttpRequest, key: str | None) -> User | None:
        if key is None or request.user.is_authenticated or is_public_route(request):
            return None
        return pending_user(request, "setup")


pending_setup_auth = PendingSetupAuth()

from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _
from ninja.security import HttpBearer

from apps.accounts.language import activate_account_language
from apps.accounts.models import User
from apps.accounts.second_factor_policy import must_set_up_first
from apps.common.errors import ApiError
from apps.devices import services


class DeviceTokenAuth(HttpBearer):
    """`Authorization: Bearer <token>` from a paired device.

    Sets `request.user` and `request.device`. A revoked or unknown token is refused
    at once with 401 `not_authenticated`, whatever else the request carries (no
    fallback to a session, no CSRF check first). A request without the header is
    left to the next authenticator. Bearer requests need no CSRF token: a browser
    never adds this header by itself.
    """

    def authenticate(self, request: HttpRequest, token: str) -> User | None:
        device = services.device_for_token(
            token, app_version=request.headers.get(services.APP_VERSION_HEADER, "")
        )
        if device is None:
            raise ApiError(401, "not_authenticated", _("Je bent niet ingelogd."))
        if must_set_up_first(device.user, now=timezone.now()):
            # Blocked, not revoked: the phone works again once the factor is on (security
            # review of PR #52, point 5).
            raise ApiError(
                401,
                "second_factor_required",
                _("Stel eerst tweestapsverificatie in op de webinterface."),
            )
        request.user = device.user
        request.device = device  # type: ignore[attr-defined]
        # From here on the account's own language choice wins over Accept-Language.
        activate_account_language(request, device.user)
        return device.user

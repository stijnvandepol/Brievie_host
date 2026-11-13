from django.http import HttpRequest
from ninja.security import HttpBearer

from apps.accounts.models import User
from apps.devices import services


class DeviceTokenAuth(HttpBearer):
    """`Authorization: Bearer <token>` from a paired device.

    Sets `request.user` and `request.device`. A revoked or unknown token does not
    authenticate; with no session either, the request gets 401 `not_authenticated`.
    Bearer requests need no CSRF token: a browser never adds this header by itself.
    """

    def authenticate(self, request: HttpRequest, token: str) -> User | None:
        device = services.device_for_token(token)
        if device is None:
            return None
        request.user = device.user
        request.device = device  # type: ignore[attr-defined]
        return device.user

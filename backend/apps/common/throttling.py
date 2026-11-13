from django.http import HttpRequest
from ninja.throttling import SimpleRateThrottle

from apps.common.net import client_ip


class ClientAddressThrottle(SimpleRateThrottle):
    """Limits attempts per client address, e.g. `ClientAddressThrottle("pairing", "10/10m")`.

    Counts live in Django's default cache, the database cache, so all gunicorn
    workers share them.
    """

    def __init__(self, scope: str, rate: str) -> None:
        self.scope = scope
        super().__init__(rate)

    def get_cache_key(self, request: HttpRequest) -> str:
        return self.cache_format % {"scope": self.scope, "ident": client_ip(request) or "unknown"}

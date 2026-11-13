from django.http import HttpRequest
from ninja.throttling import SimpleRateThrottle

from apps.common.net import client_ip


class PairingThrottle(SimpleRateThrottle):
    """10 pairing attempts per client address per 10 minutes.

    Counts in Django's default cache, which is the database cache, so all
    gunicorn workers share one count.
    """

    scope = "pairing"

    def __init__(self) -> None:
        super().__init__("10/10m")

    def get_cache_key(self, request: HttpRequest) -> str:
        return self.cache_format % {"scope": self.scope, "ident": client_ip(request) or "unknown"}

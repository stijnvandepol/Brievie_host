import time

from django.http import HttpRequest
from ninja.throttling import SimpleRateThrottle

from apps.common.net import address_bucket, client_ip


class CallerThrottle(SimpleRateThrottle):
    """Limits attempts per authenticated caller and path, e.g. per administrator and
    workspace. Somebody else, at the same address or not, cannot use up the attempts.

    With `per_path=False` one count per caller covers every path of the operations that
    share this instance (making invitations: a renewal has a new id in its path each time).
    """

    def __init__(self, scope: str, rate: str, *, per_path: bool = True) -> None:
        self.scope = scope
        self.per_path = per_path
        super().__init__(rate)

    def get_cache_key(self, request: HttpRequest) -> str:
        ident = f"{request.user.pk}:{request.path}" if self.per_path else f"{request.user.pk}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class ClientAddressThrottle(SimpleRateThrottle):
    """Limits attempts per client address, e.g. `ClientAddressThrottle("pairing", "10/10m")`.

    Counts live in Django's default cache, the database cache, so all gunicorn
    workers share them.
    """

    def __init__(self, scope: str, rate: str) -> None:
        self.scope = scope
        super().__init__(rate)

    def get_cache_key(self, request: HttpRequest) -> str:
        # An IPv6 client counts per /64 (`address_bucket`).
        return self.cache_format % {
            "scope": self.scope,
            "ident": address_bucket(client_ip(request)),
        }


class FailedAttemptThrottle(ClientAddressThrottle):
    """Counts only the attempts the service reports as failed, per client address.

    As a Ninja throttle it refuses an address that has `rate` failures in the window;
    a request that is let through is not counted. The service calls `record_failure`
    when the attempt fails, so successful sign-ins of many people behind one address
    never use up the limit.
    """

    def throttle_success(self) -> bool:
        return True

    def record_failure(self, request: HttpRequest) -> None:
        key = self.get_cache_key(request)
        now = time.time()
        history: list[float] = [
            moment for moment in self.cache.get(key, []) if moment > now - (self.duration or 0)
        ]
        history.insert(0, now)
        self.cache.set(key, history, self.duration)

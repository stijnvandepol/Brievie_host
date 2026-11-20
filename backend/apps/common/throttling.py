import threading
import time

from django.http import HttpRequest
from ninja.throttling import SimpleRateThrottle

from apps.common.net import address_bucket, client_ip


class _PerThreadState(SimpleRateThrottle):
    """Ninja keeps the request's `key`, `history` and `now` on the throttle instance
    between `allow_request`, `throttle_success` and `wait`. Ours are module-level and
    gunicorn runs several threads, so one request could write another's history under
    its key. Here they live per thread.

    Not atomic across processes: two workers can still read the same count at once.
    """

    def __init__(self, rate: str | None = None) -> None:
        self._state = threading.local()
        super().__init__(rate)

    @property
    def key(self) -> str | None:
        value: str | None = getattr(self._state, "key", None)
        return value

    @key.setter
    def key(self, value: str | None) -> None:
        self._state.key = value

    @property
    def history(self) -> list[float]:
        value: list[float] = getattr(self._state, "history", [])
        return value

    @history.setter
    def history(self, value: list[float]) -> None:
        self._state.history = value

    @property
    def now(self) -> float:
        value: float = getattr(self._state, "now", 0.0)
        return value

    @now.setter
    def now(self, value: float) -> None:
        self._state.now = value


class CallerThrottle(_PerThreadState):
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


class ClientAddressThrottle(_PerThreadState):
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

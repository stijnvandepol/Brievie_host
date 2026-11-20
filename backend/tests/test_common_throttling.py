"""Security review 2026-11-20 (identity L1): Ninja's throttles keep the request's key and
history on the instance, and the instances are module-level, shared by gunicorn's
threads. Two requests at once must not mix their counts."""

import threading

from django.core.cache.backends.locmem import LocMemCache
from django.test import RequestFactory
from ninja.throttling import SimpleRateThrottle

from apps.common.throttling import CallerThrottle, ClientAddressThrottle, FailedAttemptThrottle


def _throttle() -> ClientAddressThrottle:
    throttle = ClientAddressThrottle("test_threads", "5/1m")
    throttle.cache = LocMemCache("test-throttle-threads", {})
    return throttle


def test_another_thread_does_not_change_this_requests_key_or_history() -> None:
    throttle = _throttle()
    first = RequestFactory().get("/", REMOTE_ADDR="192.0.2.1")
    second = RequestFactory().get("/", REMOTE_ADDR="192.0.2.2")

    assert throttle.allow_request(first)
    key, history = throttle.key, list(throttle.history)
    assert key is not None

    worker = threading.Thread(target=lambda: throttle.allow_request(second))
    worker.start()
    worker.join()

    assert throttle.key == key
    assert throttle.history == history
    # Each address has its own one count in the cache.
    assert len(throttle.cache.get(key, [])) == 1
    assert len(throttle.cache.get(key.replace("192.0.2.1", "192.0.2.2"), [])) == 1


def test_every_throttle_of_ours_keeps_its_state_per_thread() -> None:
    for kind in (CallerThrottle, ClientAddressThrottle, FailedAttemptThrottle):
        throttle = kind("x", "1/1m")
        throttle.key = "mine"
        seen: list[object] = []

        def read(t: SimpleRateThrottle = throttle, out: list[object] = seen) -> None:
            out.append(getattr(t, "key", None))

        worker = threading.Thread(target=read)
        worker.start()
        worker.join()
        assert seen == [None], kind
        assert throttle.key == "mine"

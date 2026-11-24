"""What the server has seen of the ways in, for the settings page (security review of PR #53).

The page is opened at home (a web session never counts on the public route), so what
happens on the public route is remembered here: per kind, when it was last seen, in the
shared database cache for `WINDOW`. Only a kind and a time, never a name or a path. For a
request for the public name from an untrusted source also its direct peer, and only when
that is an address of a local network (your proxy's, for BRIEVIE_TRUSTED_PROXIES), never
an address on the internet. Each process writes a kind at most once per `WRITE_EVERY`, so
a busy route costs no database write per request.
"""

import ipaddress
import logging
import time
from datetime import datetime, timedelta

from django.core.cache import cache
from django.db import DatabaseError
from django.utils import timezone

logger = logging.getLogger(__name__)

WINDOW = timedelta(days=7)
WRITE_EVERY = 600.0  # seconds
KINDS = (
    # A request for the public name from a source that is not a trusted proxy.
    "public_name_untrusted",
    # Cloudflare's headers on the https port: a tunnel pointed at :443, or proxied DNS.
    "cloudflare_on_https_port",
    # A request through the tunnel listener.
    "tunnel",
    # A request from a trusted proxy that named no client (no X-Forwarded-For).
    "proxy_without_forwarding",
    # A public-route request whose Host was not the public name.
    "host_not_forwarded",
)
_written: dict[str, float] = {}


def _key(kind: str) -> str:
    return f"brievie:access:seen:{kind}"


def note(kind: str, *, peer: str = "") -> None:
    """Remember that `kind` was seen now (and `peer`, when it is a local address). A
    database error is logged by its type only and never fails the request."""
    if kind not in KINDS:
        raise ValueError(kind)
    now = time.monotonic()
    if now - _written.get(kind, -WRITE_EVERY) < WRITE_EVERY:
        return
    _written[kind] = now
    timeout = int(WINDOW.total_seconds())
    try:
        cache.set(_key(kind), timezone.now().isoformat(), timeout=timeout)
        if _is_local(peer):
            cache.set(_key(kind) + ":peer", peer, timeout=timeout)
    except DatabaseError as error:
        logger.warning("Access: could not note a route (%s)", type(error).__name__)


# Local networks: the private ranges, carrier-grade NAT (a Tailscale address), unique local
# IPv6, link-local and loopback. Not Python's `is_private`, which also counts the
# documentation ranges and other reserved blocks.
_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "127.0.0.0/8",
        "fc00::/7",
        "fe80::/10",
        "::1/128",
    )
)


def _is_local(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in _LOCAL_NETWORKS)


def last_peer(kind: str) -> str:
    """The last local peer noted with `kind`, or empty."""
    try:
        value = cache.get(_key(kind) + ":peer")
    except DatabaseError:
        return ""
    return value if isinstance(value, str) and _is_local(value) else ""


def last_seen(kind: str) -> datetime | None:
    try:
        value = cache.get(_key(kind))
    except DatabaseError:
        return None
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def forget_written() -> None:
    """For tests: every kind may be written again at once."""
    _written.clear()

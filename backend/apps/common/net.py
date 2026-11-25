import ipaddress
import logging
import socket
import time

from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger(__name__)
_not_caddy_logged_at = -60.0

# Caddy's addresses, resolved from its compose name per process: kept for `_CADDY_TTL`, and
# looked up again at once when a peer is not among them, so a lookup made before Caddy
# existed, or a recreated Caddy with a new address, is never believed stale. An empty
# answer is never kept. Only containers of the stack reach the api, so the extra lookups
# are few (the api's own healthcheck, every 5 seconds).
_CADDY_TTL = 30.0
_caddy: tuple[float, frozenset[str]] = (-_CADDY_TTL, frozenset())


def caddy_addresses(*, refresh: bool = False) -> frozenset[str]:
    """The addresses of `BRIEVIE_CADDY_HOST` (compose: `caddy`); empty when it does not
    resolve. Only a request whose socket peer is one of these is Caddy's."""
    global _caddy
    now = time.monotonic()
    if not refresh and now - _caddy[0] < _CADDY_TTL and _caddy[1]:
        return _caddy[1]
    try:
        infos = socket.getaddrinfo(settings.BRIEVIE_CADDY_HOST, None, proto=socket.IPPROTO_TCP)
        found = frozenset(str(info[4][0]) for info in infos)
    except OSError:
        found = frozenset()
    if found:
        _caddy = (now, found)
    return found


def from_caddy(request: HttpRequest) -> bool:
    """Whether Brievie's own Caddy sent this request: behind Caddy, and the socket peer is
    Caddy's address (security review of PR #53, point 2). Only then do its X-Brievie-*
    headers count; anything else that reaches the api directly gets the most restricted
    route and its own socket peer as client address."""
    if not settings.BRIEVIE_BEHIND_CADDY:
        return False
    peer = str(request.META.get("REMOTE_ADDR", "")).strip()
    if not peer:
        return False
    if peer in caddy_addresses() or peer in caddy_addresses(refresh=True):
        return True
    _log_not_caddy(peer)
    return False


def _log_not_caddy(peer: str) -> None:
    """At most once a minute per process: which peer reached the api without Caddy, and
    Caddy's known addresses (addresses only), so a wrong network is found from the log."""
    global _not_caddy_logged_at
    now = time.monotonic()
    if now - _not_caddy_logged_at < 60.0:
        return
    _not_caddy_logged_at = now
    logger.warning(
        "Access: a request reached the api without Caddy (peer %s; Caddy at %s)",
        usable_ip(peer) or "?",
        ", ".join(sorted(_caddy[1])) or "unknown",
    )


def client_ip(request: HttpRequest) -> str | None:
    """The address of the client, as seen by the first trusted proxy.

    Behind Caddy (`BRIEVIE_BEHIND_CADDY`) it is Caddy's `X-Brievie-Client-IP`. Otherwise,
    with `BRIEVIE_PROXY_COUNT = 0` (default) this is `REMOTE_ADDR`. Behind Caddy
    (count 1) it is the last `X-Forwarded-For` entry, the one Caddy added itself;
    entries before it come from the client and can be forged.
    """
    # Behind Caddy (compose): the address Caddy wrote itself after its own trusted-proxy
    # rules (X-Brievie-Client-IP, S 7.3); a client's value never gets here. Never
    # CF-Connecting-IP: only Caddy reads that, on its unpublished tunnel listener.
    remote_addr = request.META.get("REMOTE_ADDR")
    if settings.BRIEVIE_BEHIND_CADDY:
        if not from_caddy(request):
            # Not through Caddy: only the socket peer is known for sure.
            return str(remote_addr) if remote_addr else None
        caddy = str(request.META.get("HTTP_X_BRIEVIE_CLIENT_IP", "")).strip()
        if caddy:
            return caddy
    proxies: int = settings.BRIEVIE_PROXY_COUNT
    if proxies <= 0:
        return str(remote_addr) if remote_addr else None
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR", ""))
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if len(hops) < proxies:
        return str(remote_addr) if remote_addr else None
    return hops[-proxies]


def valid_client_ip(request: HttpRequest) -> str | None:
    """`client_ip(request)` when it is an IP address, else None.

    For storing in an `inet` column: an `X-Forwarded-For` entry such as `unknown` or
    `1.2.3.4:80` would make the insert fail. The throttles keep keying on `client_ip`.
    """
    return usable_ip(client_ip(request))


def usable_ip(address: str | None) -> str | None:
    """`address` normalised when PostgreSQL's `inet` can hold it, else None."""
    if not address:
        return None
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    # A zone (`fe80::1%eth0`) is valid for Python, not for PostgreSQL.
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.scope_id:
        return None
    return str(parsed)


def address_bucket(address: str | None) -> str:
    """What a limit per client address counts on: an IPv4 address itself, an IPv6
    address's /64 network (one host or customer usually holds a whole /64, so counting
    single addresses would let it rotate past every limit), else the raw value."""
    if not address:
        return "unknown"
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{parsed.exploded}/64", strict=False))
    return str(parsed)

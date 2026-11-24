"""Which way a request came, as Caddy says (S 5.6).

Caddy is the only judge of the route: it writes `X-Brievie-Route` and `X-Brievie-Peer-IP`
itself on every request it passes to the api, overwriting whatever a client sent. Django
believes them only when `BRIEVIE_BEHIND_CADDY` is true (compose sets it); without Caddy
(development) every request is private.
"""

from django.conf import settings
from django.http import HttpRequest

PRIVATE = "private"


def is_public_route(request: HttpRequest) -> bool:
    """True for the tunnel and a trusted reverse proxy. Behind Caddy, anything but exactly
    `private` counts as public: a request that reached the api without Caddy's mark gets
    the closed way, never the open one."""
    if not settings.BRIEVIE_BEHIND_CADDY:
        return False
    return request.META.get("HTTP_X_BRIEVIE_ROUTE") != PRIVATE


def peer_ip(request: HttpRequest) -> str:
    """The direct peer of the connection Caddy received (for the settings page only)."""
    if settings.BRIEVIE_BEHIND_CADDY:
        return str(request.META.get("HTTP_X_BRIEVIE_PEER_IP", "")).strip()
    return str(request.META.get("REMOTE_ADDR", "")).strip()


# The ways Caddy names in X-Brievie-Via (deploy/Caddyfile).
VIAS = ("home", "public_name", "proxy", "cloudflare", "tunnel")


def via(request: HttpRequest) -> str:
    """Which way exactly, as Caddy says; empty without Caddy or for an unknown value."""
    if not settings.BRIEVIE_BEHIND_CADDY:
        return ""
    value = str(request.META.get("HTTP_X_BRIEVIE_VIA", "")).strip()
    return value if value in VIAS else ""

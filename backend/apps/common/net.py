import ipaddress

from django.conf import settings
from django.http import HttpRequest


def client_ip(request: HttpRequest) -> str | None:
    """The address of the client, as seen by the first trusted proxy.

    With `BRIEVIE_PROXY_COUNT = 0` (default) this is `REMOTE_ADDR`. Behind Caddy
    (count 1) it is the last `X-Forwarded-For` entry, the one Caddy added itself;
    entries before it come from the client and can be forged.
    """
    remote_addr = request.META.get("REMOTE_ADDR")
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

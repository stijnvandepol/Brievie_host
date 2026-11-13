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

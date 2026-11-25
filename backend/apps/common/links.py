"""Links the server hands out (reset links, invitations) and where they can be opened."""

import ipaddress
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpRequest

# Names that only resolve inside a home network.
_HOME_SUFFIXES = (".local", ".lan", ".home.arpa")


def web_urls() -> list[str]:
    """The `BRIEVIE_PUBLIC_URLS` entries where the web interface answers: the public
    name (tunnel, own reverse proxy) only while `BRIEVIE_PUBLIC_WEB` is on (S 7.6)."""
    urls: list[str] = settings.BRIEVIE_PUBLIC_URLS
    if settings.BRIEVIE_PUBLIC_WEB:
        return list(urls)
    public: list[str] = settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS
    return [url for url in urls if url not in public]


def public_base_url(request: HttpRequest) -> str:
    """The server's address for links (invitations, password resets): the public address
    while the web interface answers there, else the first address where it does, else
    the address this request came in on. Without a trailing slash."""
    urls = web_urls()
    public: list[str] = settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS
    for url in urls:
        if url in public:
            return url.rstrip("/")
    if urls:
        return urls[0].rstrip("/")
    return request.build_absolute_uri("/").rstrip("/")


def fragment_link(request: HttpRequest, path: str, token: str) -> str:
    """A link to `path` with `token` in the fragment: a browser never sends a fragment to
    a server, so the token stays out of every access log."""
    return f"{public_base_url(request)}{path}#{token}"


def _is_home_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(_HOME_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def home_network_only(request: HttpRequest) -> bool:
    """Whether the web interface can only be reached inside a home network: every URL
    where it answers (or, without one, this request's own host) is a home name or a
    private address."""
    urls = web_urls()
    hosts = [urlsplit(url).hostname or "" for url in urls]
    if not urls:
        hosts = [urlsplit(f"//{request.get_host()}").hostname or ""]
    return all(_is_home_host(host) for host in hosts)

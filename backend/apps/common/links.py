"""Links the server hands out (reset links, invitations) and where they can be opened."""

import ipaddress
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpRequest

# Names that only resolve inside a home network.
_HOME_SUFFIXES = (".local", ".lan", ".home.arpa")


def public_base_url(request: HttpRequest) -> str:
    """The server's address for links: the first `BRIEVIE_PUBLIC_URLS` entry, else the
    address this request came in on. Without a trailing slash."""
    urls: list[str] = settings.BRIEVIE_PUBLIC_URLS
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
    """Whether the server can only be reached inside a home network: every public URL
    (or, without one, this request's own host) is a home name or a private address."""
    urls: list[str] = settings.BRIEVIE_PUBLIC_URLS
    hosts = [urlsplit(url).hostname or "" for url in urls]
    if not urls:
        hosts = [urlsplit(f"//{request.get_host()}").hostname or ""]
    return all(_is_home_host(host) for host in hosts)

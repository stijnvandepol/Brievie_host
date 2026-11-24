"""What the server says about its own addresses (S 9)."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings


@dataclass(frozen=True)
class ServerAddresses:
    # Every address the app may use, home first (BRIEVIE_PUBLIC_URLS).
    urls: list[str]
    # Those served with a public certificate: the app trusts them with the system's roots.
    public_urls: list[str]


def server_addresses() -> ServerAddresses:
    urls: list[str] = list(settings.BRIEVIE_PUBLIC_URLS)
    certificate: list[str] = settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS
    name = settings.BRIEVIE_PUBLIC_HOSTNAME.lower().rstrip(".")
    # Only an https URL of this server's own list whose host is the configured public name:
    # the app trusts system roots for exactly these (security review of PR #53, point 5).
    public = [
        url
        for url in urls
        if url in certificate and url.startswith("https://") and urlsplit(url).hostname == name
    ]
    return ServerAddresses(urls=urls, public_urls=public)

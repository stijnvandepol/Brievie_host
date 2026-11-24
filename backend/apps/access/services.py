"""What the server says about its own addresses (S 9)."""

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class ServerAddresses:
    # Every address the app may use, home first (BRIEVIE_PUBLIC_URLS).
    urls: list[str]
    # Those served with a public certificate: the app trusts them with the system's roots.
    public_urls: list[str]


def server_addresses() -> ServerAddresses:
    urls: list[str] = list(settings.BRIEVIE_PUBLIC_URLS)
    public: list[str] = settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS
    return ServerAddresses(urls=urls, public_urls=[url for url in urls if url in public])

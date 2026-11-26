"""What the settings page "Van buiten bereikbaar" shows (S 8).

Read from the settings (compose decides, the page only shows), from the admin's own
request and from what the server saw on the way in (apps.access.observed).
Never a token or secret. Brievie runs no tunnel (remote access is the user's own tool): the
page only shows the hooks (names, trusted proxies, public web) and what the server saw on
the way in. The api makes no outgoing call.
"""

import ipaddress
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from django.conf import settings
from django.db.models import Exists, OuterRef
from django.http import HttpRequest

from apps.access import observed
from apps.access.route import is_public_route, peer_ip
from apps.accounts.models import SecondFactor, User
from apps.common.net import client_ip
from apps.devices.models import Device

EXTERNAL_ACCESS_DOCS_URL = "https://brievie.com/nl/install/#publieke-toegang"
# The first app version that trusts each address the way it is served (iOS I1-I3). None
# until that release is cut: until then every paired app needs the update.
FIRST_APP_WITH_PUBLIC_ADDRESSES: tuple[int, ...] | None = None

Level = Literal["ok", "warning", "problem"]
Kind = Literal["home", "vpn", "public"]
Certificate = Literal["private_ca", "public"]

# The closed list of check codes and their level; the web holds the sentences.
CHECK_LEVELS: dict[str, Level] = {
    "hostname_loopback": "problem",
    "hostname_mdns_over_vpn": "warning",
    "public_hostname_missing": "problem",
    "public_url_overridden_without_public_host": "problem",
    # Red only when the web is on outside and its second step is somehow not required.
    "public_web_without_second_factor": "problem",
    "public_web_second_factor_required": "ok",
    "public_web_admin_without_second_factor": "warning",
    "public_web_off": "ok",
    "request_not_secure": "problem",
    "public_host_not_forwarded": "problem",
    "trusted_proxy_without_forwarding": "problem",
    "app_update_needed": "warning",
    "public_name_from_untrusted_source": "problem",
    "unknown_proxy": "problem",
}
CHECK_CODES: tuple[str, ...] = tuple(CHECK_LEVELS)


@dataclass(frozen=True)
class Address:
    url: str
    kind: Kind
    certificate: Certificate


@dataclass(frozen=True)
class Check:
    code: str
    level: Level


@dataclass(frozen=True)
class SeenAs:
    route: Literal["private", "public"]
    secure: bool
    client_ip: str
    peer_ip: str


@dataclass(frozen=True)
class ExternalAccess:
    hostname: str
    vpn_hostname: str
    public_hostname: str
    addresses: list[Address]
    public_web: bool
    trusted_proxies: list[str]
    second_factor_available: bool
    seen_as: SeenAs
    checks: list[Check]
    docs_url: str
    # The local address that last sent a proxy's headers or asked for the public name
    # without being a trusted proxy: likely your proxy's. Empty when none.
    untrusted_public_name_peer: str = ""


def _version(value: str) -> tuple[int, ...] | None:
    parts = value.strip().split(".")
    if not value.strip() or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def needs_app_update(versions: Iterable[str]) -> bool:
    """Whether any paired app predates per-address trust (no device is named)."""
    first = FIRST_APP_WITH_PUBLIC_ADDRESSES
    for value in versions:
        found = _version(value)
        if first is None or found is None or found < first:
            return True
    return False


def _active_app_versions() -> list[str]:
    return list(
        Device.objects.filter(revoked_at__isnull=True).values_list("app_version", flat=True)
    )


def _admin_without_factor() -> bool:
    """Whether an active server admin has no confirmed second factor (and so cannot sign in
    to the web from outside)."""
    confirmed = SecondFactor.objects.filter(user_id=OuterRef("id"), confirmed_at__isnull=False)
    return (
        User.objects.filter(is_server_admin=True, is_active=True)
        .exclude(Exists(confirmed))
        .exists()
    )


def _is_loopback(host: str) -> bool:
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _in_networks(address: str, networks: list[str]) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    for network in networks:
        try:
            if parsed in ipaddress.ip_network(network, strict=False):
                return True
        except ValueError:
            continue
    return False


def _addresses() -> list[Address]:
    public: list[str] = settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS
    vpn = settings.BRIEVIE_VPN_HOSTNAME.lower().rstrip(".")
    found: list[Address] = []
    for index, url in enumerate(settings.BRIEVIE_PUBLIC_URLS):
        if url in public:
            found.append(Address(url=url, kind="public", certificate="public"))
        elif index > 0 and vpn and f"//{vpn}" in url.lower():
            found.append(Address(url=url, kind="vpn", certificate="private_ca"))
        else:
            found.append(Address(url=url, kind="home", certificate="private_ca"))
    return found


def external_access_status(
    request: HttpRequest,
    *,
    app_versions: Callable[[], list[str]] | None = None,
    seen: Callable[[str], datetime | None] | None = None,
    admin_without_factor: Callable[[], bool] | None = None,
) -> ExternalAccess:
    app_versions = app_versions or _active_app_versions
    admin_without_factor = admin_without_factor or _admin_without_factor
    seen = seen or observed.last_seen
    hostname: str = settings.BRIEVIE_HOSTNAME
    public_hostname: str = settings.BRIEVIE_PUBLIC_HOSTNAME
    proxies: list[str] = list(settings.BRIEVIE_TRUSTED_PROXIES)
    public_web: bool = settings.BRIEVIE_PUBLIC_WEB
    second_factor: bool = settings.BRIEVIE_SECOND_FACTOR_AVAILABLE
    public_way = bool(proxies)

    public = is_public_route(request)
    seen_as = SeenAs(
        route="public" if public else "private",
        secure=request.is_secure(),
        client_ip=client_ip(request) or "",
        peer_ip=peer_ip(request),
    )

    codes: list[str] = []
    if _is_loopback(hostname or "localhost"):
        codes.append("hostname_loopback")
    if hostname.lower().rstrip(".").endswith(".local"):
        codes.append("hostname_mdns_over_vpn")
    if public_way and not public_hostname:
        codes.append("public_hostname_missing")
    if public_hostname and not settings.BRIEVIE_PUBLIC_CERTIFICATE_URLS:
        codes.append("public_url_overridden_without_public_host")
    # Without Caddy no request counts as public (apps.access.route), so the second step is
    # not asked outside either: say so rather than claim it always is (security review of
    # PR #56, point 6).
    second_step_outside = second_factor and bool(settings.BRIEVIE_BEHIND_CADDY)
    if public_way and public_web and not second_step_outside:
        codes.append("public_web_without_second_factor")
    if public_way and public_web and second_step_outside:
        # A password sign-in on the public route needs the second step (apps.accounts.signin).
        codes.append("public_web_second_factor_required")
        if admin_without_factor():
            codes.append("public_web_admin_without_second_factor")
    if public_way and not public_web:
        codes.append("public_web_off")
    if not seen_as.secure:
        codes.append("request_not_secure")
    # Both from what the server saw on the public route (apps.access.observed), since the page
    # is mostly opened at home (on the public route a web session counts only with the web on
    # there and after the second step); and from the admin's own request when it did come
    # that way (BRIEVIE_PUBLIC_WEB).
    host_wrong = seen("host_not_forwarded") is not None
    if public and public_hostname:
        host = urlsplit(f"//{request.get_host()}").hostname or ""
        host_wrong = host_wrong or host.rstrip(".") != public_hostname.lower().rstrip(".")
    if host_wrong:
        codes.append("public_host_not_forwarded")
    # A trusted proxy that sent no X-Forwarded-For: most likely Docker's own gateway (every
    # device in the network then looks like that address and may pick its own).
    own_request = (
        bool(proxies)
        and _in_networks(seen_as.peer_ip, proxies)
        and seen_as.client_ip == seen_as.peer_ip
    )
    if own_request or seen("proxy_without_forwarding") is not None:
        codes.append("trusted_proxy_without_forwarding")
    # Seen on the way in (apps.access.observed): the public name from a source that is not
    # your proxy (it was served as public, app-only, the direct peer counted).
    if seen("public_name_untrusted") is not None:
        codes.append("public_name_from_untrusted_source")
    # A proxy's headers from a source that is not trusted: served as public, but a proxy that
    # rewrites Host and sends none of them would not be seen (SELFHOST: trust it first).
    if seen("unknown_proxy") is not None:
        codes.append("unknown_proxy")
    if public_way and needs_app_update(app_versions()):
        codes.append("app_update_needed")

    return ExternalAccess(
        hostname=hostname,
        vpn_hostname=settings.BRIEVIE_VPN_HOSTNAME,
        public_hostname=public_hostname,
        addresses=_addresses(),
        public_web=public_web,
        trusted_proxies=proxies,
        second_factor_available=second_factor,
        seen_as=seen_as,
        checks=[Check(code=code, level=CHECK_LEVELS[code]) for code in codes],
        docs_url=EXTERNAL_ACCESS_DOCS_URL,
        untrusted_public_name_peer=(
            (observed.last_peer("unknown_proxy") if "unknown_proxy" in codes else "")
            or (
                observed.last_peer("public_name_untrusted")
                if "public_name_from_untrusted_source" in codes
                else ""
            )
        ),
    )

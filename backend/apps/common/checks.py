"""System checks for settings that agree with each other, or do not.

They run with every management command, so the `api` container (which runs
`prepare_setup_token` before gunicorn) refuses to start on a wrong configuration.
"""

import ipaddress
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning, register
from django.db import DatabaseError
from django.http.request import validate_host
from django.utils.translation import gettext as _

from apps.access.addresses import name_problems

# Every secret in deploy/.env.example contains this: a server that still uses one is
# open to anyone who has read this repository.
PLACEHOLDER_MARKER = "change-me"


@register()
def check_public_urls(
    app_configs: Iterable[AppConfig] | None, **kwargs: object
) -> list[CheckMessage]:
    """Every host in BRIEVIE_PUBLIC_URLS must be in ALLOWED_HOSTS, or Django answers 400."""
    errors: list[CheckMessage] = []
    for url in settings.BRIEVIE_PUBLIC_URLS:
        host = urlsplit(url).hostname or ""
        if ":" in host:  # an IPv6 address; ALLOWED_HOSTS spells it with brackets
            host = f"[{host}]"
        if not validate_host(host, settings.ALLOWED_HOSTS):
            errors.append(
                Error(
                    f"BRIEVIE_PUBLIC_URLS has {url}, but its host {host} is not in ALLOWED_HOSTS.",
                    hint=(
                        "Set BRIEVIE_HOSTNAME in deploy/.env to the name or IP address of this "
                        "server; ALLOWED_HOSTS and BRIEVIE_PUBLIC_URLS follow from it. If you set "
                        "them yourself, they must agree."
                    ),
                    id="brievie.E001",
                )
            )
    return errors


@register()
def check_names(app_configs: Iterable[AppConfig] | None, **kwargs: object) -> list[CheckMessage]:
    """The VPN and public names are bare host names and differ from each other and from
    the home name (Caddy would refuse a site address twice; S 6)."""
    return [
        Error(
            f"{variable} must be a bare host name (no https://, port or path) that differs "
            "from BRIEVIE_HOSTNAME and the other name.",
            hint=_(
                "Zet in deploy/.env alleen de naam, zoals brievie.voorbeeld.nl, en gebruik "
                "elke naam maar één keer."
            ),
            id="brievie.E002",
        )
        for variable in name_problems(
            hostname=settings.BRIEVIE_HOSTNAME,
            vpn_hostname=settings.BRIEVIE_VPN_HOSTNAME,
            public_hostname=settings.BRIEVIE_PUBLIC_HOSTNAME,
        )
    ]


@register()
def check_external_access(
    app_configs: Iterable[AppConfig] | None, **kwargs: object
) -> list[CheckMessage]:
    """Warnings, never a refusal: the home network must keep working whatever is set."""
    messages: list[CheckMessage] = []
    public_way = bool(settings.BRIEVIE_TRUSTED_PROXIES)
    # Without Caddy no request counts as public (apps.access.route): nothing asks the second
    # step outside then.
    second_step_outside = settings.BRIEVIE_SECOND_FACTOR_AVAILABLE and settings.BRIEVIE_BEHIND_CADDY
    if public_way and settings.BRIEVIE_PUBLIC_WEB and not second_step_outside:
        messages.append(
            Warning(
                "BRIEVIE_PUBLIC_WEB is on: the web sign-in is reachable from the internet "
                "without a second step.",
                hint=_(
                    "Inloggen op de webinterface kan van buiten zonder tweede stap. Zet "
                    "BRIEVIE_PUBLIC_WEB uit, of gebruik een VPN."
                ),
                id="brievie.W002",
            )
        )
    if settings.BRIEVIE_PUBLIC_HOSTNAME and not public_way:
        messages.append(
            Warning(
                "BRIEVIE_PUBLIC_HOSTNAME is set, but not BRIEVIE_TRUSTED_PROXIES: requests for "
                "that name are served app-only, every visitor counted by the address of "
                "whatever forwards them.",
                hint=_(
                    "Zet BRIEVIE_TRUSTED_PROXIES op het adres van je tunnel of reverse proxy. "
                    "Dat adres staat thuis op de pagina Van buiten bereikbaar."
                ),
                id="brievie.W005",
            )
        )
    return messages


# The widest range one reverse proxy needs; wider only with BRIEVIE_TRUSTED_PROXIES_WIDE.
NARROWEST_PREFIX = {4: 24, 6: 64}
# Docker's default bridge networks: their gateway is what every forwarded port looks like.
_DOCKER_NETWORKS = ipaddress.ip_network("172.16.0.0/12")
ROUTE_TABLE = Path("/proc/net/route")


def _hex_address(value: str) -> ipaddress.IPv4Address:
    # /proc/net/route writes addresses as little-endian hexadecimal.
    return ipaddress.IPv4Address(int.from_bytes(bytes.fromhex(value), "little"))


def docker_gateways(table: Path = ROUTE_TABLE) -> set[ipaddress.IPv4Address]:
    """The gateways of this container's own networks, whatever their range: the default
    gateway, and the first address of every network it is on (Docker's gateway). Empty
    outside Linux or when the table cannot be read."""
    try:
        lines = table.read_text().splitlines()[1:]
    except OSError:
        return set()
    found: set[ipaddress.IPv4Address] = set()
    for line in lines:
        fields = line.split()
        if len(fields) < 8:
            continue
        try:
            destination, gateway, mask = (
                _hex_address(f) for f in (fields[1], fields[2], fields[7])
            )
        except ValueError:
            continue
        if int(gateway):
            found.add(gateway)
        elif int(destination):
            network = ipaddress.IPv4Network(f"{destination}/{mask}", strict=False)
            found.add(network.network_address + 1)
    return found


def trusted_proxy_problem(entry: str, *, allow_wide: bool) -> str:
    """Why `entry` of BRIEVIE_TRUSTED_PROXIES is refused: "invalid", "everything", "wide",
    or "" when it may stand."""
    try:
        network = ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return "invalid"
    if network.prefixlen == 0:
        return "everything"
    if network.prefixlen < NARROWEST_PREFIX[network.version] and not allow_wide:
        return "wide"
    return ""


@register()
def check_trusted_proxies(
    app_configs: Iterable[AppConfig] | None, **kwargs: object
) -> list[CheckMessage]:
    """BRIEVIE_TRUSTED_PROXIES names your proxy, not a network: a wide range lets every device
    in it pick its own client address (security review of PR #53, point 4). An error stops
    the api, and Caddy (which waits for a healthy api) with it: closed, not open."""
    messages: list[CheckMessage] = []
    allow_wide: bool = settings.BRIEVIE_TRUSTED_PROXIES_WIDE
    gateways = docker_gateways(ROUTE_TABLE) if settings.BRIEVIE_TRUSTED_PROXIES else set()
    for entry in settings.BRIEVIE_TRUSTED_PROXIES:
        problem = trusted_proxy_problem(entry, allow_wide=allow_wide)
        if problem:
            messages.append(
                Error(
                    f"BRIEVIE_TRUSTED_PROXIES has {entry!r} ({problem}): name your reverse "
                    "proxy's own address, at most a /24 (IPv4) or /64 (IPv6).",
                    hint=_(
                        "Zet in BRIEVIE_TRUSTED_PROXIES alleen het adres van je reverse proxy, "
                        "zoals 192.168.1.5/32. Een groter bereik kan alleen met "
                        "BRIEVIE_TRUSTED_PROXIES_WIDE=true, alles nooit."
                    ),
                    id="brievie.E003",
                )
            )
            continue
        network = ipaddress.ip_network(entry, strict=False)
        first = network.network_address
        default_range_gateway = (
            network.version == 4
            and network.num_addresses == 1
            and first in _DOCKER_NETWORKS
            and int(first) % 256 == 1
        )
        # The gateway of one of this container's own networks, in any range (a custom
        # network such as 192.168.x.0/24 or 10.x.0.0/16 too).
        own_gateway = any(gateway in network for gateway in gateways)
        if default_range_gateway or own_gateway:
            messages.append(
                Warning(
                    f"BRIEVIE_TRUSTED_PROXIES has {entry}, which looks like Docker's own gateway.",
                    hint=_(
                        "Via Docker's eigen gateway lijkt elk apparaat in je netwerk hetzelfde "
                        "adres te hebben, en kan het zijn eigen adres opgeven. Zet het adres van "
                        "je reverse proxy zelf."
                    ),
                    id="brievie.W006",
                )
            )
    return messages


@register()
def check_database_passwords(
    app_configs: Iterable[AppConfig] | None, **kwargs: object
) -> list[CheckMessage]:
    """Warn when a database password is still the placeholder from deploy/.env.example.

    A warning, not a refusal: a database volume that was created with the placeholder
    keeps it until its password is changed in PostgreSQL itself.
    """
    if settings.DEBUG:
        return []
    configs = [("DATABASE_URL", settings.DATABASES["default"])]
    if settings.DATABASE_OWNER is not None:
        configs.append(("DATABASE_OWNER_URL", settings.DATABASE_OWNER))
    return [
        Warning(
            f"The password in {name} is still the placeholder from deploy/.env.example.",
            hint=_("Choose a new password, see 'Run the whole stack' in the README."),
            id="brievie.W001",
        )
        for name, config in configs
        if PLACEHOLDER_MARKER in str(config.get("PASSWORD") or "")
    ]


@register()
def check_second_factor_rule(
    app_configs: Iterable[AppConfig] | None, **kwargs: object
) -> list[CheckMessage]:
    """Warn when a rule asks for two-step sign-in while no server admin has it yet: the
    first sign-in after the grace period leads them straight to setting it up.

    Reads the database only when a rule is set; a database that is not there yet (the
    first `migrate`) gives no warning.
    """
    if settings.BRIEVIE_REQUIRE_SECOND_FACTOR == "none":
        return []
    from apps.accounts.models import SecondFactor  # models are not ready at import time

    try:
        has_one = SecondFactor.objects.filter(
            user__is_server_admin=True, user__is_active=True, confirmed_at__isnull=False
        ).exists()
    except DatabaseError:
        return []
    if has_one:
        return []
    return [
        Warning(
            _("Nog geen serverbeheerder heeft tweestapsverificatie aan."),
            hint=_(
                "Zet het aan onder Beveiliging, of houd een herstelcode bij de hand. Op de "
                "server wist `manage.py clear_second_factor` het voor wie de telefoon kwijt is."
            ),
            id="brievie.W004",
        )
    ]

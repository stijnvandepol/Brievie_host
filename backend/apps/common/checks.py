"""System checks for settings that agree with each other, or do not.

They run with every management command, so the `api` container (which runs
`prepare_setup_token` before gunicorn) refuses to start on a wrong configuration.
"""

from collections.abc import Iterable
from urllib.parse import urlsplit

from django.apps import AppConfig
from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning, register
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


# Cloudflare's free plan accepts request bodies up to this size (S 7.5).
CLOUDFLARE_MAX_BODY_BYTES = 100_000_000


@register()
def check_external_access(
    app_configs: Iterable[AppConfig] | None, **kwargs: object
) -> list[CheckMessage]:
    """Warnings, never a refusal: the home network must keep working whatever is set."""
    messages: list[CheckMessage] = []
    tunnel: bool = settings.BRIEVIE_TUNNEL_TOKEN_SET
    public_way = tunnel or bool(settings.BRIEVIE_TRUSTED_PROXIES)
    if public_way and settings.BRIEVIE_PUBLIC_WEB and not settings.BRIEVIE_SECOND_FACTOR_AVAILABLE:
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
                "BRIEVIE_PUBLIC_HOSTNAME is set, but neither a tunnel token nor "
                "BRIEVIE_TRUSTED_PROXIES: requests for that name are served app-only, every "
                "visitor counted by the address of whatever forwards them.",
                hint=_(
                    "Zet BRIEVIE_TRUSTED_PROXIES op het adres van je reverse proxy, of stel de "
                    "tunnel in. Het adres van je proxy staat thuis op de pagina Van buiten "
                    "bereikbaar."
                ),
                id="brievie.W004",
            )
        )
    if tunnel and settings.BRIEVIE_MAX_UPLOAD_BYTES > CLOUDFLARE_MAX_BODY_BYTES:
        messages.append(
            Warning(
                "BRIEVIE_MAX_UPLOAD_BYTES is larger than Cloudflare accepts (100 MB).",
                hint=_(
                    "Grote scans komen niet door de tunnel. Zet BRIEVIE_MAX_UPLOAD_BYTES op "
                    "100000000 of minder."
                ),
                id="brievie.W003",
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

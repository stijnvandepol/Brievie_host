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

# The values in deploy/.env.example: a server that still uses them is open to anyone
# who has read this repository.
DATABASE_PASSWORD_PLACEHOLDERS = frozenset({"change-me-app", "change-me-owner"})


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
        if config.get("PASSWORD") in DATABASE_PASSWORD_PLACEHOLDERS
    ]

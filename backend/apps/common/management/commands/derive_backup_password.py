from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from apps.common import crypto


class Command(BaseCommand):
    help = (
        "Print the back-up password derived from BRIEVIE_MASTER_KEY (for deploy/.env). "
        "Runs in the container, so the master key is never a process argument on the host."
    )

    # ./brievie starts this in a new container (run --no-deps), PostgreSQL may be down:
    # no system checks, they read database features (GeneratedField).
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute

    def handle(self, *args: object, **options: object) -> None:
        try:
            key = crypto.master_key()
        except ImproperlyConfigured as exc:
            # The settings' own message is English and meant for developers.
            raise CommandError(
                _(
                    "BRIEVIE_MASTER_KEY in deploy/.env ontbreekt of is geen 64 tekens lang "
                    "(cijfers en a tot en met f). Zonder de juiste sleutel zijn de "
                    "documenten niet te lezen."
                )
            ) from exc
        self.stdout.write(crypto.derive_backup_password(key))

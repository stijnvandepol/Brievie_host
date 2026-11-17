from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from apps.common import crypto


class Command(BaseCommand):
    help = (
        "Print the back-up password derived from BRIEVIE_MASTER_KEY (for deploy/.env). "
        "Runs in the container, so the master key is never a process argument on the host."
    )

    def handle(self, *args: object, **options: object) -> None:
        try:
            key = crypto.master_key()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(crypto.derive_backup_password(key))

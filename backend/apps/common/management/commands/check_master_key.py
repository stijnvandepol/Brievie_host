from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from apps.common import crypto


class Command(BaseCommand):
    help = "Fail when BRIEVIE_MASTER_KEY is missing or malformed (run before the api and worker)."

    def handle(self, *args: object, **options: object) -> None:
        try:
            crypto.master_key()
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc

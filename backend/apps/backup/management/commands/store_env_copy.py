import sys

from django.core.management.base import BaseCommand, CommandError

from apps.backup.envcopy import store_env_copy
from apps.backup.errors import BackupError


class Command(BaseCommand):
    help = "Store the copy of deploy/.env (read from stdin) in the back-up configuration folder."

    def handle(self, *args: object, **options: object) -> None:
        try:
            store_env_copy(sys.stdin.buffer.read().decode("utf-8"))
        except BackupError as error:
            raise CommandError(error.message) from error

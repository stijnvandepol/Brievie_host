from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.translation import gettext as _

from apps.backup import restore
from apps.backup.errors import BackupError


class Command(BaseCommand):
    help = (
        "Check that a run of the back-up still has both its database and its files "
        "(read with --no-lock; changes nothing). Exit 0: it can be restored, 1: not."
    )

    # ./brievie starts this in a new container (run --no-deps), PostgreSQL may be down:
    # no system checks, they read database features (GeneratedField).
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--run", required=True, help="The run id of the back-up.")

    def handle(self, *args: object, **options: object) -> None:
        try:
            found = restore.check_run(str(options["run"]))
        except BackupError as error:
            raise CommandError(error.message, returncode=1) from error
        self.stdout.write(
            _("De back-up %(run)s is compleet: de database en de bestanden zijn er allebei.")
            % {"run": found.run_id}
        )

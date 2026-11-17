from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.backup import restore
from apps.backup.errors import BackupError


class Command(BaseCommand):
    help = (
        "Load the database dump of a back-up into an empty database, as the superuser "
        "(DATABASE_SUPERUSER_URL), and close the back-up runs it brought back as open. "
        "Runs in a one-off container, after restore_fetch wrote the restored .env."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--run", help="The run id of the back-up (default: the newest).")
        parser.add_argument("--dbname", default="brievie", help="The empty database to load.")

    def handle(self, *args: object, **options: object) -> None:
        run = options["run"]
        try:
            closed = restore.restore_database(
                run=None if run is None else str(run), dbname=str(options["dbname"])
            )
        except BackupError as error:
            raise CommandError(error.message, returncode=1) from error
        self.stdout.write(f"interrupted_runs={closed}")

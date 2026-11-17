from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.translation import gettext as _

from apps.backup import restore
from apps.backup.errors import BackupError

# Caddy's internal authority in its data volume, as the admin container mounts it.
AUTHORITY_TARGET = Path("/caddy-data/caddy/pki/authorities/local")


class Command(BaseCommand):
    help = (
        "Put the document files of a back-up back, and Caddy's authority (before Caddy "
        "starts). --replace empties the documents first. Runs in a one-off container."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--run", help="The run id of the back-up (default: the newest).")
        parser.add_argument(
            "--replace", action="store_true", help="Remove the documents that are there first."
        )

    def handle(self, *args: object, **options: object) -> None:
        run = options["run"]
        try:
            restored = restore.restore_files(
                run=None if run is None else str(run),
                authority_target=AUTHORITY_TARGET,
                clear_documents=bool(options["replace"]),
            )
        except BackupError as error:
            raise CommandError(error.message, returncode=1) from error
        self.stdout.write(
            _(
                "Opgeruimd na het terugzetten: %(count)d onderdelen die geen gewoon bestand "
                "of map waren (zoals snelkoppelingen)."
            )
            % {"count": restored.removed}
        )
        if not restored.authority_placed:
            self.stdout.write(
                _("De back-up heeft geen eigen certificaat; Caddy houdt zijn huidige certificaat.")
            )

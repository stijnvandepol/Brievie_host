import os

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.translation import gettext as _

from apps.backup import safepaths, services
from apps.backup.errors import BackupError


class Command(BaseCommand):
    help = "Prepare the back-up destination for the first time (runs in a one-off container)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--allow-same-disk",
            action="store_true",
            help="Allow the back-up on the same disk as the documents.",
        )
        parser.add_argument(
            "--update-fallback",
            action="store_true",
            help=(
                "Prepare the Docker volume update-backup, which ./brievie mounts at /backup "
                "for this call: an update's emergency back-up on a server without a "
                "destination (same disk by design)."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            safepaths.require_container()
        except BackupError as error:
            raise CommandError(error.message, returncode=1) from error
        try:
            services.initialise_destination(
                allow_same_disk=bool(options["allow_same_disk"]),
                fallback=bool(options["update_fallback"]),
            )
        except BackupError as error:
            # For ./brievie: the code on stderr, before the sentence.
            if os.environ.get("BRIEVIE_WRAPPER") == "1":
                self.stderr.write(f"code={error.code}")
            raise CommandError(error.message) from error
        self.stdout.write(_("De back-up is ingesteld."))

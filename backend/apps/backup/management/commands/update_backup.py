import os

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext as _

from apps.backup import safepaths, services
from apps.backup.errors import BackupError
from apps.backup.models import BackupRun


class Command(BaseCommand):
    help = (
        "The emergency back-up of an update on a server without a destination, made now "
        "into the volume update-backup (which ./brievie mounts at /backup for this call; "
        "prepared by backup_init --update-fallback). Keeps only its newest run and never "
        "counts as a back-up. Exit 0: made (prints run_id= for ./brievie), 1: failed."
    )

    # ./brievie starts this in a new container (run --no-deps), PostgreSQL may be down:
    # no system checks, they read database features (GeneratedField).
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--label", required=True, help="pre-update-X.Y.Z")

    def handle(self, *args: object, **options: object) -> None:
        wrapper = os.environ.get("BRIEVIE_WRAPPER") == "1"
        try:
            # It prunes the fallback: only ever inside the container.
            safepaths.require_container()
            run = services.run_update_fallback(label=str(options["label"]))
        except BackupError as error:
            if wrapper:
                self.stderr.write(f"code={error.code}")
            raise CommandError(error.message, returncode=1) from error
        if wrapper:
            self.stdout.write(f"run_id={run.id}")
        if run.status != BackupRun.Status.SUCCEEDED:
            if wrapper:
                self.stderr.write(f"code={run.reason_code or 'backup_failed'}")
            raise CommandError(run.reason or _("De back-up is niet gelukt."), returncode=1)
        size = filesizeformat(run.size_bytes or 0)
        self.stdout.write(_("De noodback-up is gemaakt (%(size)s).") % {"size": size})

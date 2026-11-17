from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext as _

from apps.backup import services
from apps.backup.models import BackupRun

# Six hours: a first back-up of a large archive to a slow destination.
DEFAULT_TIMEOUT = 21600


class Command(BaseCommand):
    help = (
        "Make (or with --check: check) a back-up through the keeper and wait for it. "
        "Exit 0: done, 1: failed, 2: not picked up or still running."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--check", action="store_true", help="Check the back-up instead of making one."
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=DEFAULT_TIMEOUT,
            help="Seconds to wait for the run to finish.",
        )

    def handle(self, *args: object, **options: object) -> None:
        check = bool(options["check"])
        kind = BackupRun.Kind.CHECK if check else BackupRun.Kind.BACKUP
        run = services.request_run(kind)
        self.stdout.write(_("Controle gestart…") if check else _("Back-up gestart…"))
        try:
            run = services.wait_for_run(run.id, timeout=float(str(options["timeout"])))
        except TimeoutError:
            run.refresh_from_db()
            if run.status == BackupRun.Status.QUEUED:
                message = _("De back-up is niet opgepakt. Draait de back-updienst (keeper)?")
            else:
                message = _(
                    "De back-up is nog bezig. Kijk later met backup_status of hij gelukt is."
                )
            raise CommandError(message, returncode=2) from None
        if run.status != BackupRun.Status.SUCCEEDED:
            raise CommandError(run.reason or _("De back-up is niet gelukt."), returncode=1)
        if check:
            self.stdout.write(_("De controle is gelukt."))
        else:
            size = filesizeformat(run.size_bytes or 0)
            self.stdout.write(_("De back-up is gelukt (%(size)s).") % {"size": size})

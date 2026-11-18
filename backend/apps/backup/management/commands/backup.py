import os

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
            "--no-prune",
            action="store_true",
            help="The safety back-up of a restore: apply no retention (forget nothing).",
        )
        parser.add_argument(
            "--timeout",
            type=int,
            default=DEFAULT_TIMEOUT,
            help="Seconds to wait for the run to finish.",
        )

    def handle(self, *args: object, **options: object) -> None:
        check = bool(options["check"])
        no_prune = bool(options["no_prune"])
        if check and no_prune:
            raise CommandError(_("--no-prune hoort bij een back-up, niet bij --check."))
        kind = BackupRun.Kind.CHECK if check else BackupRun.Kind.BACKUP
        requested_by = services.RESTORE_SAFETY if no_prune else "cli"
        run = services.request_run(kind, requested_by=requested_by)
        self.stdout.write(_("Controle gestart…") if check else _("Back-up gestart…"))
        try:
            run = services.wait_for_run(run.id, timeout=float(str(options["timeout"])))
        except TimeoutError:
            run.refresh_from_db()
            if run.status == BackupRun.Status.QUEUED:
                self._code("not_picked_up")
                message = _("De back-up is niet opgepakt. Draait de back-updienst (keeper)?")
            else:
                self._code("still_running")
                message = _(
                    "De back-up is nog bezig. Kijk later met backup_status of hij gelukt is."
                )
            raise CommandError(message, returncode=2) from None
        if run.status != BackupRun.Status.SUCCEEDED:
            self._code(run.reason_code or "backup_failed")
            raise CommandError(run.reason or _("De back-up is niet gelukt."), returncode=1)
        if check:
            self.stdout.write(_("De controle is gelukt."))
        else:
            size = filesizeformat(run.size_bytes or 0)
            self.stdout.write(_("De back-up is gelukt (%(size)s).") % {"size": size})

    def _code(self, code: str) -> None:
        """For ./brievie (BRIEVIE_WRAPPER=1): the failure's code, on stderr, before the sentence."""
        if os.environ.get("BRIEVIE_WRAPPER") == "1":
            self.stderr.write(f"code={code}")

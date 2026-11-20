from uuid import UUID

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.translation import gettext as _

from apps.updates import services
from apps.updates.release import ReleaseError


class Command(BaseCommand):
    help = "Record a finished update (./brievie update does, after the health check)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--from", dest="from_version", required=True)
        parser.add_argument("--to", dest="to_version", required=True)
        parser.add_argument("--backup-run", default="")

    def handle(self, *args: object, **options: object) -> None:
        run_text = str(options["backup_run"])
        try:
            run_id = UUID(run_text) if run_text else None
            update = services.record_update(
                from_version=str(options["from_version"]),
                to_version=str(options["to_version"]),
                backup_run_id=run_id,
            )
        except (ReleaseError, ValueError):
            raise CommandError(_("Een versie ziet eruit als 0.3.0."), returncode=2) from None
        self.stdout.write(
            _("Vastgelegd: %(from)s → %(to)s")
            % {"from": update.from_version, "to": update.to_version}
        )

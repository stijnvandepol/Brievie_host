from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext as _

from apps.backup import safepaths, sweep
from apps.backup.errors import BackupError


class Command(BaseCommand):
    help = (
        "List page files no document refers to (counts and total size only); "
        "with --delete, remove them. Files younger than 24 hours are never touched."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--delete", action="store_true", help="Delete the files found.")

    def handle(self, *args: object, **options: object) -> None:
        if options["delete"]:
            try:
                safepaths.require_container()
            except BackupError as error:
                raise CommandError(error.message, returncode=1) from error
        try:
            result = sweep.sweep(delete=bool(options["delete"]))
        except BackupError as error:
            raise CommandError(error.message) from None
        if not result.orphans:
            self.stdout.write(_("Geen losse bestanden gevonden."))
            return
        size = filesizeformat(sum(orphan.size for orphan in result.orphans))
        if options["delete"]:
            self.stdout.write(
                _("%(count)d losse bestanden gevonden (%(size)s), %(deleted)d verwijderd.")
                % {"count": len(result.orphans), "size": size, "deleted": result.deleted}
            )
        else:
            self.stdout.write(
                _(
                    "%(count)d losse bestanden gevonden (%(size)s). "
                    "Voeg --delete toe om ze te verwijderen."
                )
                % {"count": len(result.orphans), "size": size}
            )

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.backup import verify
from apps.backup.errors import BackupError


class Command(BaseCommand):
    help = (
        "Check a restored installation against the manifest of its back-up: counts per "
        "workspace, every page file present, one page per workspace opened with the "
        "master key (BRIEVIE_MASTER_KEY), the authority's fingerprint. Reads as the "
        "superuser (DATABASE_SUPERUSER_URL). Exit 1 with the problems when any."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dbname", required=True, help="The restored database.")
        parser.add_argument("--storage-root", required=True, help="The restored documents.")
        parser.add_argument("--manifest", required=True, help="The manifest.json of the run.")
        parser.add_argument("--authority-dir", help="The restored authority (root.crt).")

    def handle(self, *args: object, **options: object) -> None:
        authority_dir = options["authority_dir"]
        try:
            report = verify.verify_installation(
                dbname=str(options["dbname"]),
                storage_root=Path(str(options["storage_root"])),
                manifest_path=Path(str(options["manifest"])),
                authority_dir=None if authority_dir is None else Path(str(authority_dir)),
            )
        except BackupError as error:
            raise CommandError(error.message, returncode=1) from error
        self.stdout.write(f"workspaces={report.workspaces}")
        self.stdout.write(f"pages_opened={report.pages_opened}")
        if report.problems:
            for problem in report.problems:
                self.stderr.write(problem)
            count = len(report.problems)
            raise CommandError(
                ngettext(
                    "Het herstel is niet compleet: %(count)d probleem gevonden.",
                    "Het herstel is niet compleet: %(count)d problemen gevonden.",
                    count,
                )
                % {"count": count},
                returncode=1,
            )
        self.stdout.write(_("Het herstel is gecontroleerd. Alles klopt."))

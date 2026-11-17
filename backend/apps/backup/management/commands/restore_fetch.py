from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.translation import gettext as _

from apps.backup import restore
from apps.backup.errors import BackupError
from apps.backup.manifest import MANIFEST_NAME, manifest_from_json

# Exit codes for ./brievie: 2 = the key or password does not fit, 3 = a newer back-up.
_EXIT_CODES = {"wrong_password": 2, "wrong_master_key": 2, "newer_backup": 3}


def _owner(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    uid, separator, gid = value.partition(":")
    if not (separator and uid.isdigit() and gid.isdigit()):
        raise CommandError(_("Geef de eigenaar op als UID:GID, bijvoorbeeld 1000:1000."))
    return int(uid), int(gid)


class Command(BaseCommand):
    help = (
        "Open the back-up with the master key (BRIEVIE_MASTER_KEY) and write the restored "
        ".env and the manifest to --out. A separate back-up password comes from "
        "BRIEVIE_BACKUP_PASSWORD. Runs in a one-off container."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--run", help="The run id of the back-up (default: the newest).")
        parser.add_argument("--out", required=True, help="Folder for env and manifest.json.")
        parser.add_argument("--out-owner", help="UID:GID that gets the written files.")
        parser.add_argument(
            "--backup-dir-host", help="The back-up folder on the host, for BRIEVIE_BACKUP_DIR."
        )

    def handle(self, *args: object, **options: object) -> None:
        owner = _owner(options["out_owner"])  # type: ignore[arg-type]
        out_dir = Path(str(options["out"]))
        run = options["run"]
        backup_dir_host = options["backup_dir_host"]
        try:
            found = restore.fetch(
                run=None if run is None else str(run),
                master_key_hex=str(settings.BRIEVIE_MASTER_KEY),
                separate_password=str(settings.BRIEVIE_BACKUP_PASSWORD) or None,
                backup_dir_host=None if backup_dir_host is None else str(backup_dir_host),
                out_dir=out_dir,
                out_owner=owner,
            )
        except BackupError as error:
            raise CommandError(error.message, returncode=_EXIT_CODES.get(error.code, 1)) from error
        manifest = manifest_from_json((out_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        self.stdout.write(f"run_id={found.run_id}")
        self.stdout.write(f"created_at={manifest.created_at.isoformat()}")
        self.stdout.write(f"hostname={manifest.hostname}")
        self.stdout.write(f"brievie_version={manifest.brievie_version}")

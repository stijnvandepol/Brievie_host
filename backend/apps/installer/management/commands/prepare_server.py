import sys

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.translation import gettext as _

from apps.backup.errors import BackupError
from apps.installer import prepare, runtime


class Command(BaseCommand):
    help = (
        "The start of a compose install (the `init` service): roles and database, the version "
        "gate, the freeze, the emergency back-up, migrations and the record of the version. "
        "Exit 0: ready; 1: refused or failed (the sentence says what)."
    )

    # Runs before the database is judged: the system checks would open it.
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute

    def handle(self, *args: object, **options: object) -> None:
        if (
            settings.DATABASE_OWNER is None
            or settings.DATABASES["default"] is not settings.DATABASE_OWNER
        ):
            self.stderr.write(
                _("prepare_server draait als schema-eigenaar (BRIEVIE_DB_ROLE=owner).")
            )
            sys.exit(1)
        try:
            deps = runtime.build_deps(runtime.own_environ(), self.stdout.write, self.stderr.write)
            code = prepare.run(deps)
        except (runtime.ConfigurationError, BackupError) as error:
            self.stderr.write(str(error.message if isinstance(error, BackupError) else error))
            code = 1
        if code:
            sys.exit(code)

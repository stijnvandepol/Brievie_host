from django.core.management.base import BaseCommand

from apps.backup import sheet


class Command(BaseCommand):
    help = "Write the recovery sheet (HTML, holds the master key) to stdout. Logs nothing."

    # ./brievie starts this in a new container (run --no-deps), PostgreSQL may be down:
    # no system checks, they read database features (GeneratedField).
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute

    def handle(self, *args: object, **options: object) -> None:
        self.stdout.write(sheet.render_sheet(sheet.sheet_data()))

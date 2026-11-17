from django.core.management.base import BaseCommand

from apps.backup import sheet


class Command(BaseCommand):
    help = "Write the recovery sheet (HTML, holds the master key) to stdout. Logs nothing."

    def handle(self, *args: object, **options: object) -> None:
        self.stdout.write(sheet.render_sheet(sheet.sheet_data()))

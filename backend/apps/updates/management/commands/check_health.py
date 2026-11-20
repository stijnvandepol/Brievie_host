from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.translation import gettext as _

from apps.updates import services


class Command(BaseCommand):
    help = (
        "Inside the api container after an update: does the server answer 200 with this "
        "version? Tries for about a minute. Exit 0, or 1 with a sentence."
    )
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--expect-version", required=True)

    def handle(self, *args: object, **options: object) -> None:
        verdict = services.wait_until_healthy(str(options["expect_version"]))
        if verdict:
            raise CommandError(_("De server antwoordt niet goed na het bijwerken."), returncode=1)

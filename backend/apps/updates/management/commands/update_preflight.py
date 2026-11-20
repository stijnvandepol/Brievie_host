from django.core.management.base import BaseCommand, CommandError
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext as _

from apps.updates import services


class Command(BaseCommand):
    help = "Is there room on this machine for an update? Exit 0 (silent) or 1 with a sentence."

    def handle(self, *args: object, **options: object) -> None:
        found = services.preflight()
        if found.free_bytes < found.needed_bytes:
            raise CommandError(
                _(
                    "Er is te weinig vrije ruimte om bij te werken: nodig %(needed)s, "
                    "vrij %(free)s. Er is niets veranderd."
                )
                % {
                    "needed": filesizeformat(found.needed_bytes),
                    "free": filesizeformat(found.free_bytes),
                },
                returncode=1,
            )

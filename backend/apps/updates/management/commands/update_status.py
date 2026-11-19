from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.updates import services


class Command(BaseCommand):
    help = "The last update, for ./brievie status."

    def handle(self, *args: object, **options: object) -> None:
        last = services.last_update()
        if last is None:
            self.stdout.write(_("Nog nooit bijgewerkt met ./brievie update"))
            return
        when = timezone.localtime(last.finished_at).strftime("%d-%m-%Y %H:%M")
        self.stdout.write(
            _("Laatst bijgewerkt: %(from)s → %(to)s op %(when)s")
            % {"from": last.from_version, "to": last.to_version, "when": when}
        )

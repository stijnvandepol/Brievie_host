from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from apps.accounts.models import User, normalise_email
from apps.accounts.second_factor import clear_factor
from apps.audit.services import AuditActor
from apps.common.errors import ApiError


class Command(BaseCommand):
    help = (
        "Clear the two-step verification of an account whose phone is lost (a server admin "
        "who cannot sign in). Every session of the account ends and its paired devices are "
        "unpaired (--keep-devices keeps them); the account gets a notice and the log records "
        "it."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--email", required=True, help="The account's email address.")
        parser.add_argument(
            "--keep-devices",
            action="store_true",
            help="Keep the account's paired devices (only when the lost phone had none).",
        )

    def handle(self, *args: object, email: str, keep_devices: bool, **options: object) -> None:
        user = User.objects.filter(email=normalise_email(email)).first()
        if user is None:
            raise CommandError(_("Er is geen account met dit e-mailadres."))
        try:
            revoked = clear_factor(
                user,
                revoke_devices=not keep_devices,
                via="command",
                actor=AuditActor.system(),
                actor_name="",
            )
        except ApiError as exc:
            raise CommandError(exc.message) from exc
        self.stdout.write(
            _(
                "De tweestapsverificatie is gewist. Alle sessies van dit account zijn "
                "beëindigd; %(count)s apparaten zijn ontkoppeld."
            )
            % {"count": revoked}
        )

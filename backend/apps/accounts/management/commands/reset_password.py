from argparse import ArgumentParser
from getpass import getpass

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.translation import gettext as _

from apps.accounts.models import User, normalise_email
from apps.accounts.resets import reset_password_by_command


class Command(BaseCommand):
    help = (
        "Choose a new password for an account, for a server admin who lost theirs. Asks the "
        "password twice; every session of the account ends."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        # Never the password: a command-line argument ends up in the shell history.
        parser.add_argument("--email", required=True, help="The account's email address.")

    def handle(self, *args: object, email: str, **options: object) -> None:
        if not User.objects.filter(email=normalise_email(email)).exists():
            raise CommandError(_("Er is geen account met dit e-mailadres."))
        password = getpass(_("Nieuw wachtwoord: "))
        if getpass(_("Nog een keer: ")) != password:
            raise CommandError(_("De wachtwoorden zijn niet gelijk. Er is niets veranderd."))
        try:
            reset_password_by_command(email=email, password=password)
        except User.DoesNotExist as exc:
            raise CommandError(_("Er is geen account met dit e-mailadres.")) from exc
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from exc
        self.stdout.write(
            _("Het wachtwoord is ingesteld. Alle sessies van dit account zijn beëindigd.")
        )

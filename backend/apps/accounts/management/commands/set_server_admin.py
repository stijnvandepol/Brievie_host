from argparse import ArgumentParser

from django.core.management.base import BaseCommand, CommandError
from django.utils.functional import Promise
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.accounts.account_state import set_server_admin
from apps.audit.services import AuditActor

# The service's refusal codes, as one Dutch sentence each.
REFUSALS: dict[str, Promise] = {
    "unknown_account": gettext_lazy("Er is geen account met dit e-mailadres."),
    "account_inactive": gettext_lazy(
        "Dit account is uitgeschakeld. Nodig de persoon eerst opnieuw uit."
    ),
    "last_server_admin": gettext_lazy("Er is altijd minstens één serverbeheerder nodig."),
}


class Command(BaseCommand):
    help = (
        "Make an account a server admin, or with --remove take that away. The server always "
        "keeps at least one active server admin."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--email", required=True, help="The account's email address.")
        parser.add_argument(
            "--remove", action="store_true", help="Take the server-admin role away."
        )

    def handle(self, *args: object, email: str, remove: bool, **options: object) -> None:
        try:
            user = set_server_admin(email=email, grant=not remove, actor=AuditActor.system())
        except ValueError as exc:
            reason = str(exc)
            if reason not in REFUSALS:
                raise
            raise CommandError(str(REFUSALS[reason])) from exc
        if remove:
            self.stdout.write(_("%(name)s is geen serverbeheerder meer.") % {"name": user.name})
        else:
            self.stdout.write(_("%(name)s is nu serverbeheerder.") % {"name": user.name})

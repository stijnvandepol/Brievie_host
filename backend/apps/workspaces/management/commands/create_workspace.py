"""`manage.py create_workspace --name NAME --kind home|business --admin-email ADDRESS`.

A second workspace is a server admin's deliberate act (S 5.2): made on the server, with
the kind's values (R40). The account of that address becomes its admin; without an
account, the first admin is invited and the link is printed once.
"""

from argparse import ArgumentParser

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.translation import gettext as _

from apps.accounts.models import User, normalise_email
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.workspaces.invitations import INVITATION_PATH, invite_first_admin
from apps.workspaces.models import Workspace
from apps.workspaces.services import create_workspace, create_workspace_without_admin


def invitation_link(token: str) -> str:
    """The link with the token in its fragment, on the server's first public address;
    a path only when none is set."""
    urls: list[str] = settings.BRIEVIE_PUBLIC_URLS
    base = urls[0].rstrip("/") if urls else ""
    return f"{base}{INVITATION_PATH}#{token}"


class Command(BaseCommand):
    help = (
        "Make a workspace with the values of its kind (home: Thuis, business: Organisatie). "
        "The account of --admin-email becomes its admin; without an account an invitation "
        "for that address is made and its link printed once."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--name", required=True, help="The workspace's name.")
        parser.add_argument("--kind", required=True, help="home (Thuis) or business (Organisatie).")
        parser.add_argument("--admin-email", required=True, help="The first admin's address.")

    def handle(
        self, *args: object, name: str, kind: str, admin_email: str, **options: object
    ) -> None:
        if kind not in Workspace.Kind.values:
            raise CommandError(
                _("Kies als soort home (Thuis) of business (Organisatie)."), returncode=2
            )
        name = name.strip()
        if not name:
            raise CommandError(_("Geef de werkruimte een naam."), returncode=2)
        address = normalise_email(admin_email)
        if not address:
            raise CommandError(_("Dit e-mailadres klopt niet."), returncode=2)
        admin = User.objects.filter(email=address, is_active=True).first()
        with transaction.atomic():
            if admin is not None:
                workspace = create_workspace(name=name, kind=kind, admin=admin)
                token = ""
            else:
                workspace = create_workspace_without_admin(name=name, kind=kind)
                try:
                    _invitation, token = invite_first_admin(workspace=workspace, email=address)
                except ValidationError as exc:
                    raise CommandError(_("Dit e-mailadres klopt niet."), returncode=2) from exc
            record(
                AuditCode.WORKSPACE_CREATED,
                actor=AuditActor.system(),
                target=AuditTarget(TargetKind.WORKSPACE, workspace.id, workspace.name),
                values={"kind": kind},
            )
        if admin is not None:
            self.stdout.write(
                _("Werkruimte %(name)s is gemaakt. %(admin)s is beheerder.")
                % {"name": workspace.name, "admin": admin.name}
            )
            return
        self.stdout.write(
            _(
                "Werkruimte %(name)s is gemaakt. Stuur deze link naar %(email)s; die wordt "
                "de eerste beheerder. De link werkt 7 dagen en wordt maar één keer getoond:"
            )
            % {"name": workspace.name, "email": address}
        )
        self.stdout.write(invitation_link(token))

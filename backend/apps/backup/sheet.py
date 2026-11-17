"""The recovery sheet: one printable page with the master key and the restore steps.

It runs in the one-off admin container, which holds the master key. The key goes
into the rendered page and nowhere else: not into a log, not into an exception.
"""

from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.backup import destination
from apps.backup.errors import BackupError
from apps.common import crypto
from apps.devices import pki

GROUP_SIZE = 4


def group_key(hex_key: str) -> str:
    """The key in groups of four characters, lower case, one space between groups."""
    key = hex_key.lower()
    return " ".join(key[i : i + GROUP_SIZE] for i in range(0, len(key), GROUP_SIZE))


@dataclass(frozen=True)
class SheetData:
    hostname: str
    created_on: date
    master_key_groups: str
    separate_password: str | None
    authority_sha256: str | None
    destination: str
    restore_steps: list[str]


def _restore_steps() -> list[str]:
    return [
        _("Installeer Brievie op de nieuwe computer."),
        _(
            "Sluit de back-upschijf aan. Gebruik je een back-up op afstand? "
            "Zorg dan dat je het adres van die back-up bij de hand hebt."
        ),
        _("Typ ./brievie restore en geef deze sleutel als er om gevraagd wordt."),
        _("Wacht tot je Herstel gelukt ziet. Open Brievie daarna zoals je gewend bent."),
    ]


def _destination() -> str:
    not_set = _("Nog niet ingesteld")
    try:
        kind = destination.destination_kind()
    except BackupError:
        return not_set
    if kind == "folder":
        return str(settings.BRIEVIE_BACKUP_DIR_HOST).strip() or not_set
    if kind in ("rest", "s3"):
        return str(settings.BRIEVIE_BACKUP_REPOSITORY).strip() or not_set
    return not_set


def sheet_data() -> SheetData:
    key = crypto.master_key()
    configured: str = settings.BRIEVIE_BACKUP_PASSWORD
    derived = crypto.derive_backup_password(key)
    separate = configured if configured and configured != derived else None
    return SheetData(
        hostname=settings.BRIEVIE_HOSTNAME,
        created_on=timezone.localdate(),
        master_key_groups=group_key(key.hex()),
        separate_password=separate,
        authority_sha256=pki.ca_certificate_sha256(),
        destination=_destination(),
        restore_steps=_restore_steps(),
    )


def render_sheet(data: SheetData) -> str:
    return render_to_string("backup/recovery_sheet.html", {"data": data})

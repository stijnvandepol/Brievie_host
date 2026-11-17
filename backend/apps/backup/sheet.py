"""The recovery sheet: one printable page with the master key and the restore steps.

It runs in the one-off admin container, which holds the master key. The key goes
into the rendered page and nowhere else: not into a log, not into an exception.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from apps.backup import destination
from apps.backup.errors import BackupError
from apps.common import crypto
from apps.devices import pki

GROUP_SIZE = 4
_HEX = re.compile(r"[0-9a-fA-F]+")


def group_key(hex_key: str) -> str:
    """The key in groups of four characters, lower case, one space between groups."""
    key = hex_key.lower()
    return " ".join(key[i : i + GROUP_SIZE] for i in range(0, len(key), GROUP_SIZE))


@dataclass(frozen=True)
class SheetData:
    hostname: str
    created_on: date
    master_key_groups: str = field(repr=False)
    separate_password: str | None = field(repr=False)
    authority_sha256: str | None
    destination: str
    restore_steps: list[str]


def _restore_steps(*, separate_password: bool) -> list[str]:
    if separate_password:
        ask = _(
            "Typ ./brievie restore. Brievie vraagt dan om twee dingen: "
            "deze sleutel en het aparte wachtwoord."
        )
    else:
        ask = _("Typ ./brievie restore en geef deze sleutel als er om gevraagd wordt.")
    return [
        _("Installeer Brievie op de nieuwe computer."),
        _(
            "Sluit de back-upschijf aan. Gebruik je een back-up op afstand? "
            "Zorg dan dat je het adres van die back-up bij de hand hebt."
        ),
        ask,
        _("Wacht tot je Herstel gelukt. ziet. Open Brievie daarna zoals je gewend bent."),
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


def _separate_password(derived: str) -> str | None:
    """The configured back-up password when it is a real, separate one (else None).

    Same rule as `destination.backup_password`, plus: blank is not set, and the
    derived value (any letter case) is already on the sheet as the key.
    """
    value: str = str(settings.BRIEVIE_BACKUP_PASSWORD).strip()
    if not value or "change-me" in value or value.lower() == derived:
        return None
    return group_key(value) if _HEX.fullmatch(value) else value


def sheet_data() -> SheetData:
    key = crypto.master_key()
    separate = _separate_password(crypto.derive_backup_password(key))
    with translation.override("nl"):
        return SheetData(
            hostname=settings.BRIEVIE_HOSTNAME,
            created_on=timezone.localdate(),
            master_key_groups=group_key(key.hex()),
            separate_password=separate,
            authority_sha256=pki.ca_certificate_sha256(),
            destination=_destination(),
            restore_steps=_restore_steps(separate_password=separate is not None),
        )


def render_sheet(data: SheetData) -> str:
    with translation.override("nl"):
        return render_to_string("backup/recovery_sheet.html", {"data": data})

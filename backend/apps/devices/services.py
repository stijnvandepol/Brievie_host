import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.common.errors import ApiError
from apps.devices.models import Device, PairingCode

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_VALIDITY = timedelta(minutes=10)
LAST_SEEN_INTERVAL = timedelta(minutes=1)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalise_code(code: str) -> str:
    """ "abcd-2345", "ABCD2345" and " abcd 2345 " are the same code."""
    return "".join(ch for ch in code.upper() if ch not in "- ")


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def create_pairing_code(user: User) -> tuple[PairingCode, str]:
    """A new code for `user`, shown as XXXX-XXXX; their older unused codes stop working."""
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    with transaction.atomic():
        PairingCode.objects.filter(user=user, used_at__isnull=True).delete()
        pairing_code = PairingCode.objects.create(
            user=user,
            code_hash=sha256_hex(code),
            expires_at=timezone.now() + CODE_VALIDITY,
        )
    return pairing_code, format_code(code)


def qr_payload(code: str, *, ca_sha256: str | None) -> str:
    """What the QR code holds: a JSON string the app parses (version 1)."""
    payload = {
        "v": 1,
        "urls": list(settings.BRIEVIE_PUBLIC_URLS),
        "code": code,
        "ca_sha256": ca_sha256,
    }
    return json.dumps(payload, separators=(",", ":"))


def pair_device(*, code: str, device_name: str) -> tuple[Device, str]:
    """Exchange a valid, unused code for a device and its bearer token (shown once).

    The code is looked up by its hash, so the database compares hashes, never the
    code itself; the final comparison is constant-time as well.
    """
    name = device_name.strip()
    if not name:
        raise ApiError(422, "validation_error", _("Geef het apparaat een naam."))
    code_hash = sha256_hex(normalise_code(code))
    with transaction.atomic():
        pairing_code = (
            PairingCode.objects.select_for_update()
            .select_related("user")
            .filter(code_hash=code_hash, used_at__isnull=True, expires_at__gt=timezone.now())
            .first()
        )
        if (
            pairing_code is None
            or not hmac.compare_digest(pairing_code.code_hash, code_hash)
            or not pairing_code.user.is_active
        ):
            raise ApiError(
                400,
                "pairing_code_invalid",
                _("Deze koppelcode klopt niet of is verlopen. Maak een nieuwe code aan."),
            )
        pairing_code.used_at = timezone.now()
        pairing_code.save(update_fields=["used_at", "updated_at"])
        token = secrets.token_urlsafe(32)
        device = Device.objects.create(
            user=pairing_code.user, name=name, token_hash=sha256_hex(token)
        )
    return device, token


def device_for_token(token: str) -> Device | None:
    """The active device of an active user that holds this bearer token, if any."""
    device = (
        Device.objects.select_related("user")
        .filter(token_hash=sha256_hex(token), revoked_at__isnull=True, user__is_active=True)
        .first()
    )
    if device is None:
        return None
    now = timezone.now()
    if device.last_seen_at is None or now - device.last_seen_at >= LAST_SEEN_INTERVAL:
        with transaction.atomic():
            Device.objects.filter(pk=device.pk).update(last_seen_at=now)
        device.last_seen_at = now
    return device


def list_devices(user: User) -> list[Device]:
    return list(Device.objects.filter(user=user, revoked_at__isnull=True).order_by("created_at"))


def revoke_device(user: User, device_id: UUID) -> None:
    with transaction.atomic():
        revoked = Device.objects.filter(id=device_id, user=user, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        if revoked == 0:
            raise ApiError(404, "device_not_found", _("We kunnen dit apparaat niet vinden."))

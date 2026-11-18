import hashlib
import hmac
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User, UserSession
from apps.accounts.services import current_user
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.crypto import DecryptionError
from apps.common.errors import ApiError
from apps.devices import pki
from apps.devices.models import Device, PairingChallenge, PairingCode
from apps.devices.proof import (
    derive_proof_key,
    normalise_code,
    open_proof_key,
    pairing_proof,
    seal_proof_key,
    trust_bindings,
)

logger = logging.getLogger(__name__)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CODE_VALIDITY = timedelta(minutes=10)
LAST_SEEN_INTERVAL = timedelta(minutes=1)
# The app sends its version in this header (D 7.1); a 1c app sends none.
APP_VERSION_HEADER = "X-Brievie-App-Version"
APP_VERSION_INTERVAL = timedelta(hours=24)
_APP_VERSION = re.compile(r"[0-9A-Za-z.+()\- ]{1,50}")
CHALLENGE_VALIDITY = timedelta(minutes=5)
# After this many wrong proofs the code is burned, for the QR path as well.
MAX_FAILED_PROOFS = 5
# Compared against when there is no usable code, so that path does the same work.
_NO_KEY = bytes(32)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def clean_app_version(raw: str) -> str:
    """The app version as reported, trimmed; "" when it is not a plain version string."""
    value = raw.strip()
    return value if _APP_VERSION.fullmatch(value) else ""


def create_pairing_code(
    user: User, *, paired_via: str = UserSession.Method.PASSWORD
) -> tuple[PairingCode, str]:
    """A new code for `user`, shown as XXXX-XXXX; their older unused codes stop working.

    `paired_via` is the sign-in method of the web session that asked for it.
    """
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    pairing_id = uuid4()
    # Slow on purpose (key stretching); outside the transaction.
    sealed_key = seal_proof_key(derive_proof_key(code, pairing_id), pairing_id)
    now = timezone.now()
    with transaction.atomic():
        _forget_expired_proof_keys(now)
        PairingCode.objects.filter(user=user, used_at__isnull=True).delete()
        pairing_code = PairingCode.objects.create(
            id=pairing_id,
            user=user,
            code_hash=sha256_hex(code),
            expires_at=now + CODE_VALIDITY,
            paired_via=paired_via,
            proof_key_sealed=sealed_key,
        )
    return pairing_code, format_code(code)


@dataclass(frozen=True)
class IssuedPairingCode:
    code: str
    expires_at: datetime
    qr_payload: str
    server_link: str


def issue_pairing_code(request: HttpRequest) -> IssuedPairingCode:
    """A new pairing code for the signed-in caller, with the QR payload that carries it
    and the link (without the code) that opens the app."""
    user = current_user(request)
    method = (
        UserSession.objects.filter(session_key=request.session.session_key)
        .values_list("method", flat=True)
        .first()
    )
    pairing_code, code = create_pairing_code(user, paired_via=method or UserSession.Method.PASSWORD)
    return IssuedPairingCode(
        code=code,
        expires_at=pairing_code.expires_at,
        qr_payload=qr_payload(code),
        server_link=server_link(pairing_code.id),
    )


def qr_payload(code: str) -> str:
    """What the QR code holds: a JSON string the app parses (version 1).

    `ca_sha256` pins Caddy's private root; it is null when there is none (yet).
    """
    payload = {
        "v": 1,
        "urls": list(settings.BRIEVIE_PUBLIC_URLS),
        "code": code,
        "ca_sha256": pki.ca_certificate_sha256(),
    }
    return json.dumps(payload, separators=(",", ":"))


def server_link(pairing_id: UUID) -> str:
    """The `brievie://server#…` link that opens the app (D 6.2, D 20).

    It carries no secret: the server's addresses, its CA pin (left out without a private
    CA) and the id of the pairing code, which identifies the code for a proof but does
    not redeem it. Values in the fragment are form-encoded.
    """
    fields: list[tuple[str, str]] = [("v", "1")]
    fields += [("url", url) for url in settings.BRIEVIE_PUBLIC_URLS]
    pin = pki.ca_certificate_sha256()
    if pin is not None:
        fields.append(("ca_sha256", pin))
    fields.append(("pairing", str(pairing_id)))
    return "brievie://server#" + urlencode(fields)


def pair_device(
    *, code: str, device_name: str, app_version: str = "", client_ip: str | None = None
) -> tuple[Device, str]:
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
        return _create_device(
            pairing_code, name=name, app_version=app_version, client_ip=client_ip, via="code"
        )


def _create_device(
    pairing_code: PairingCode, *, name: str, app_version: str, client_ip: str | None, via: str
) -> tuple[Device, str]:
    """Use up `pairing_code` (locked by the caller, inside its transaction) and create the
    device with a new bearer token; log it as `device_paired` with `via`."""
    now = timezone.now()
    pairing_code.used_at = now
    pairing_code.proof_key_sealed = None
    pairing_code.save(update_fields=["used_at", "proof_key_sealed", "updated_at"])
    token = secrets.token_urlsafe(32)
    version = clean_app_version(app_version)
    device = Device.objects.create(
        user=pairing_code.user,
        name=name,
        token_hash=sha256_hex(token),
        app_version=version,
        app_version_at=now if version else None,
        paired_via=pairing_code.paired_via,
    )
    record(
        AuditCode.DEVICE_PAIRED,
        actor=AuditActor.for_user(pairing_code.user, client_ip=client_ip),
        target=AuditTarget(kind=TargetKind.DEVICE, id=device.id, name=device.name),
        values={"via": via},
    )
    return device, token


def create_pairing_challenge() -> PairingChallenge:
    """A new one-time nonce for a pairing proof, valid for five minutes. Expired
    challenges and the keys of expired codes are cleared first."""
    now = timezone.now()
    with transaction.atomic():
        _forget_expired_challenges(now)
        _forget_expired_proof_keys(now)
        return PairingChallenge.objects.create(
            nonce=secrets.token_urlsafe(32), expires_at=now + CHALLENGE_VALIDITY
        )


def forget_expired_pairing_material() -> None:
    """For the periodic job: what `create_pairing_challenge` clears, also when nobody asks
    for a code or a challenge."""
    now = timezone.now()
    with transaction.atomic():
        _forget_expired_challenges(now)
        _forget_expired_proof_keys(now)


def _forget_expired_challenges(now: datetime) -> None:
    PairingChallenge.objects.filter(expires_at__lt=now).delete()


def _forget_expired_proof_keys(now: datetime) -> None:
    PairingCode.objects.filter(expires_at__lte=now, proof_key_sealed__isnull=False).update(
        proof_key_sealed=None
    )


def pair_device_with_proof(
    *,
    pairing_id: UUID,
    nonce: str,
    proof: str,
    device_name: str,
    app_version: str = "",
    client_ip: str | None = None,
) -> tuple[Device, str]:
    """Pair from a link (D 6.2): the app proves it knows the code, bound to the CA or host
    it connected to, without sending the code.

    The nonce is used up first, in a block that stays written when the answer is an
    error. An unknown, expired, used, burned or keyless code answers exactly like a
    wrong proof for a live one (same status and body); only a live code counts the
    wrong proof, and its fifth burns it for the QR path too.
    """
    name = device_name.strip()
    if not name:
        raise ApiError(422, "validation_error", _("Geef het apparaat een naam."))
    now = timezone.now()
    with transaction.atomic():
        challenge = PairingChallenge.objects.select_for_update().filter(nonce=nonce).first()
        challenge_valid = (
            challenge is not None and challenge.used_at is None and challenge.expires_at > now
        )
        if challenge is not None and challenge_valid:
            challenge.used_at = now
            challenge.save(update_fields=["used_at", "updated_at"])
    if challenge is None or not challenge_valid:
        raise ApiError(
            400,
            "pairing_challenge_invalid",
            _("Het koppelen duurde te lang. Probeer het opnieuw."),
        )
    with transaction.atomic():
        pairing_code = (
            PairingCode.objects.select_for_update()
            .select_related("user")
            .filter(
                id=pairing_id,
                used_at__isnull=True,
                expires_at__gt=now,
                proof_key_sealed__isnull=False,
                user__is_active=True,
            )
            .first()
        )
        matches = _proof_matches(pairing_code, nonce=challenge.nonce, proof=proof)
        if pairing_code is not None and matches:
            return _create_device(
                pairing_code, name=name, app_version=app_version, client_ip=client_ip, via="proof"
            )
        if pairing_code is not None:
            _count_failed_proof(pairing_code, now)
    raise ApiError(
        400,
        "pairing_code_invalid",
        _("Deze koppelcode klopt niet of is verlopen. Maak een nieuwe code aan."),
    )


def _proof_matches(pairing_code: PairingCode | None, *, nonce: str, proof: str) -> bool:
    """Whether `proof` is this code's proof for `nonce` and one of the server's trust
    bindings. Every candidate is compared in constant time, also after a match, and
    without a usable code the same comparisons run against a dummy key."""
    key = _NO_KEY
    if pairing_code is not None and pairing_code.proof_key_sealed is not None:
        try:
            key = open_proof_key(bytes(pairing_code.proof_key_sealed), pairing_code.id)
        except DecryptionError:
            # Another master key since the code was made; the code cannot pair by proof.
            logger.warning("A pairing code's proof key could not be opened")
            pairing_code = None
    given = proof.encode("utf-8")
    matched = False
    for binding in sorted(trust_bindings()):
        expected = pairing_proof(key, nonce=nonce, trust_binding=binding).encode("ascii")
        matched |= hmac.compare_digest(expected, given)
    return matched and pairing_code is not None


def _count_failed_proof(pairing_code: PairingCode, now: datetime) -> None:
    pairing_code.failed_proofs += 1
    fields = ["failed_proofs", "updated_at"]
    if pairing_code.failed_proofs >= MAX_FAILED_PROOFS:
        pairing_code.used_at = now
        pairing_code.proof_key_sealed = None
        fields += ["used_at", "proof_key_sealed"]
    pairing_code.save(update_fields=fields)


def device_for_token(token: str, *, app_version: str = "") -> Device | None:
    """The active device of an active user that holds this bearer token, if any.

    `last_seen_at` is written at most once a minute and the reported app version at most
    once a day (D 7.1), in one update when both are due.
    """
    device = (
        Device.objects.select_related("user")
        .filter(token_hash=sha256_hex(token), revoked_at__isnull=True, user__is_active=True)
        .first()
    )
    if device is None:
        return None
    now = timezone.now()
    changes: dict[str, datetime | str] = {}
    if device.last_seen_at is None or now - device.last_seen_at >= LAST_SEEN_INTERVAL:
        changes["last_seen_at"] = now
    version = clean_app_version(app_version)
    if (
        version
        and version != device.app_version
        and (device.app_version_at is None or now - device.app_version_at >= APP_VERSION_INTERVAL)
    ):
        changes["app_version"] = version
        changes["app_version_at"] = now
    if changes:
        with transaction.atomic():
            Device.objects.filter(pk=device.pk).update(**changes)
        for field, value in changes.items():
            setattr(device, field, value)
    return device


def list_devices(user: User) -> list[Device]:
    return list(Device.objects.filter(user=user, revoked_at__isnull=True).order_by("created_at"))


def revoke_device(user: User, device_id: UUID, *, actor: AuditActor) -> None:
    """Revoke `user`'s device and log `device_revoked` with `actor`; 404 when there is no
    such active device."""
    with transaction.atomic():
        device = (
            Device.objects.select_for_update()
            .filter(id=device_id, user=user, revoked_at__isnull=True)
            .first()
        )
        if device is None:
            raise ApiError(404, "device_not_found", _("We kunnen dit apparaat niet vinden."))
        device.revoked_at = timezone.now()
        device.save(update_fields=["revoked_at", "updated_at"])
        record(
            AuditCode.DEVICE_REVOKED,
            actor=actor,
            target=AuditTarget(kind=TargetKind.DEVICE, id=device.id, name=device.name),
        )


def ca_certificate_pem() -> str:
    pem = pki.load_ca_certificate_pem()
    if pem is None:
        raise ApiError(404, "no_private_ca", _("Deze server gebruikt geen eigen certificaat."))
    return pem

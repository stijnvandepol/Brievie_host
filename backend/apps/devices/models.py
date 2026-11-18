from django.conf import settings
from django.db import models

from apps.accounts.models import UserSession
from apps.common.models import BaseModel


class PairingCode(BaseModel):
    """A one-time code that lets a phone pair with the user's account.

    Only the SHA-256 of the normalised code (no dash, upper case) is stored, plus, for
    pairing from a link, the key derived from it (`apps.devices.proof`), sealed with the
    master key; the key is cleared when the code is used, burned or expired.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pairing_codes"
    )
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    # How the web session that made the code signed in; copied to the device.
    paired_via = models.CharField(
        max_length=10,
        choices=UserSession.Method.choices,
        default=UserSession.Method.PASSWORD,
    )
    # Sealed derived key for proofs; None for a code made before 1d (QR only) and once
    # the code is used, burned or expired.
    proof_key_sealed = models.BinaryField(null=True)
    failed_proofs = models.PositiveSmallIntegerField(default=0)

    def __str__(self) -> str:
        return f"Pairing code of {self.user_id}"


class PairingChallenge(BaseModel):
    """A one-time nonce for a pairing proof; not secret. A server table: it is asked for
    before anybody is known (no user, no workspace)."""

    nonce = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return "Pairing challenge"


class Device(BaseModel):
    """A paired phone. Only the SHA-256 of its bearer token is stored."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="devices"
    )
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=64, unique=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    # What the app reports (D 7.1), refreshed at most once a day; empty for a 1c app.
    app_version = models.CharField(max_length=50, blank=True, default="")
    app_version_at = models.DateTimeField(null=True, blank=True)
    # The sign-in method of the web session that made the pairing code (D 9.4).
    paired_via = models.CharField(
        max_length=10,
        choices=UserSession.Method.choices,
        default=UserSession.Method.PASSWORD,
    )

    def __str__(self) -> str:
        return self.name

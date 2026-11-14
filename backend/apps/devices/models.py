from django.conf import settings
from django.db import models

from apps.common.models import BaseModel


class PairingCode(BaseModel):
    """A one-time code that lets a phone pair with the user's account.

    Only the SHA-256 of the normalised code (no dash, upper case) is stored.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pairing_codes"
    )
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Pairing code of {self.user_id}"


class Device(BaseModel):
    """A paired phone. Only the SHA-256 of its bearer token is stored."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="devices"
    )
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=64, unique=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name

"""Authenticated encryption (AES-256-GCM) for document files and stored secrets."""

import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

KEY_BYTES = 32
_MAGIC = b"BRV1"
_NONCE_BYTES = 12
_TAG_BYTES = 16
# Label of the back-up password derived from the master key. Never change it: existing
# back-ups are encrypted with the password it gives (docs/DECISIONS.md, B1).
BACKUP_PASSWORD_INFO = b"brievie/backup-password/v1"
# Labels of the keys that seal second-factor secrets and key the recovery-code hashes
# (two-step sign-in, S 2). Never change them: stored secrets and hashes depend on them.
SECOND_FACTOR_INFO = b"brievie/second-factor/v1"
RECOVERY_CODES_INFO = b"brievie/recovery-codes/v1"


class DecryptionError(Exception):
    """The data was not sealed with this key and this context, or it was changed."""


def new_key() -> bytes:
    return os.urandom(KEY_BYTES)


def seal(key: bytes, plaintext: bytes, *, aad: bytes) -> bytes:
    """Encrypt `plaintext`; `aad` binds the result to its place (a page, a workspace)."""
    nonce = os.urandom(_NONCE_BYTES)
    return _MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def open_sealed(key: bytes, sealed: bytes, *, aad: bytes) -> bytes:
    header = len(_MAGIC) + _NONCE_BYTES
    if not sealed.startswith(_MAGIC) or len(sealed) < header + _TAG_BYTES:
        raise DecryptionError("Not sealed data.")
    nonce = sealed[len(_MAGIC) : header]
    try:
        return AESGCM(key).decrypt(nonce, sealed[header:], aad)
    except InvalidTag:
        raise DecryptionError("Wrong key or context, or the data was changed.") from None


def master_key() -> bytes:
    """The key that wraps every workspace key, from BRIEVIE_MASTER_KEY.

    Read on first use, not at start-up, so commands that never touch documents
    (migrate, export_openapi) run without it. Losing it loses every document.
    """
    value: str = settings.BRIEVIE_MASTER_KEY
    try:
        key = bytes.fromhex(value)
    except ValueError:
        key = b""
    if len(key) != KEY_BYTES:
        raise ImproperlyConfigured(
            "BRIEVIE_MASTER_KEY must be 64 hexadecimal characters (32 bytes). "
            "Generate one with `openssl rand -hex 32` and keep a copy in a safe place: "
            "without it the stored documents cannot be read."
        )
    return key


def derive_backup_password(master_key: bytes) -> str:
    """The default back-up (restic) password: 64 lower-case hex characters.

    HKDF-Expand only: the master key is already 32 uniformly random bytes (RFC 5869
    section 3.3). The result equals HMAC-SHA256(master_key, BACKUP_PASSWORD_INFO + 0x01),
    which only `manage.py derive_backup_password` (in the container) computes.
    """
    derived = HKDFExpand(algorithm=SHA256(), length=KEY_BYTES, info=BACKUP_PASSWORD_INFO)
    return derived.derive(master_key).hex()


def _derive(master_key: bytes, info: bytes) -> bytes:
    """A 32-byte key for one purpose, HKDF-Expand of the master key (as the back-up
    password): the purpose is bound in the key, and no key opens another's data."""
    return HKDFExpand(algorithm=SHA256(), length=KEY_BYTES, info=info).derive(master_key)


def derive_second_factor_key(master_key: bytes) -> bytes:
    """The key that seals every account's authenticator secret."""
    return _derive(master_key, SECOND_FACTOR_INFO)


def derive_recovery_code_key(master_key: bytes) -> bytes:
    """The key of the HMAC under which recovery codes are stored."""
    return _derive(master_key, RECOVERY_CODES_INFO)


def second_factor_aad(user_id: UUID) -> bytes:
    """Binds a sealed authenticator secret to its account: it opens for no other."""
    return f"second-factor/{user_id}".encode("ascii")

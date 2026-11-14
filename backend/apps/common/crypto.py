"""Authenticated encryption (AES-256-GCM) for document files and stored secrets."""

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

KEY_BYTES = 32
_MAGIC = b"BRV1"
_NONCE_BYTES = 12
_TAG_BYTES = 16


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

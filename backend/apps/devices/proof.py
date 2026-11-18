"""Pairing from a link by proof (D 6.2 as changed by D 20), protocol version 1.

A phone that followed a `brievie://server#…` link never sends the typed pairing code.
It fetches a one-time nonce and sends

    proof = HMAC-SHA256(key, "brievie-pair-v1" || nonce || trust binding)   (lower-case hex)
    key   = PBKDF2-HMAC-SHA256(normalised code, "brievie-pair-v1" || pairing id, 600,000, 32)

where the pairing id is the code's row id (lower case, with hyphens) and the trust
binding is the SHA-256 of the CA certificate the app pinned for this connection (64
lower-case hex) or, for a publicly trusted certificate, the host name it connected to.
A relay with another CA or host cannot produce a proof this server accepts, and never
sees the code. The iOS app implements the same bytes; `tests/fixtures/pairing_proof_v1.json`
is the shared test vector. Never change a constant here: make a version 2 instead.

The server keeps only the derived key, sealed with the master key, for the code's life.
"""

import hashlib
import hmac
import ipaddress
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings

from apps.common import crypto
from apps.devices import pki

PROOF_CONTEXT = b"brievie-pair-v1"
PROOF_KDF_ITERATIONS = 600_000
PROOF_KEY_BYTES = 32


def normalise_code(code: str) -> str:
    """ "abcd-2345", "ABCD2345" and " abcd 2345 " are the same code."""
    return "".join(ch for ch in code.upper() if ch not in "- ")


def derive_proof_key(code: str, pairing_id: UUID) -> bytes:
    """The key both sides derive from the code; slow on purpose (the code has ~40 bits).
    The server derives it only from codes it made itself (ASCII)."""
    salt = PROOF_CONTEXT + str(pairing_id).encode("ascii")
    password = normalise_code(code).encode("ascii")
    return hashlib.pbkdf2_hmac("sha256", password, salt, PROOF_KDF_ITERATIONS, PROOF_KEY_BYTES)


def pairing_proof(key: bytes, *, nonce: str, trust_binding: str) -> str:
    """64 lower-case hex characters. The nonce has a fixed length (43), so the
    concatenation is unambiguous."""
    message = PROOF_CONTEXT + nonce.encode("ascii") + trust_binding.encode("ascii")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def trust_bindings() -> frozenset[str]:
    """What this server accepts as the app's trust binding: the private CA's fingerprint
    (when there is one) and the host of every public URL that a public certificate
    authority can certify (`public_host_binding`). A home server whose addresses are all
    `.local` names or private addresses binds by its CA pin only."""
    bindings: set[str] = set()
    pin = pki.ca_certificate_sha256()
    if pin is not None:
        bindings.add(pin)
    for url in settings.BRIEVIE_PUBLIC_URLS:
        binding = public_host_binding(urlsplit(url).hostname or "")
        if binding is not None:
            bindings.add(binding)
    return frozenset(bindings)


# Names that no public certificate authority certifies: special-use and home-network
# suffixes (RFC 6761, RFC 6762, RFC 8375, ICANN's .internal).
_UNCERTIFIABLE_SUFFIXES = (
    ".local",
    ".lan",
    ".home.arpa",
    ".internal",
    ".localhost",
    ".test",
    ".invalid",
    ".example",
)


def public_host_binding(host: str) -> str | None:
    """The trust binding for `host` (a URL's host, as `urlsplit(url).hostname` gives
    it), or None when no public certificate authority can certify it.

    Normalised as the app must normalise the host it connected to: lower case, the
    A-label (IDNA) form of an international name, no port, no brackets around an IPv6
    address, no trailing dot; an IP address in its compressed form. The app applies UTS 46
    (Foundation's IDNA); this side uses Python's IDNA 2003 codec, so a name where the two
    differ (such as one with "ß") does not match and simply fails to pair by link.

    None for: `.local`, `.lan`, `.home.arpa`, `.internal`, `.localhost`, `.test`,
    `.invalid` and `.example` names, `localhost` and other single-label names, and IP
    addresses that are not global (private, loopback, link-local, shared, unique-local,
    unspecified, documentation). A global IP address is kept: public authorities do
    certify those, and only a server with such a certificate is reached at one.
    """
    host = host.strip().strip("[]").rstrip(".").lower()
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        return str(address) if address.is_global else None
    try:
        name = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if "." not in name or any(
        name.endswith(suffix) or name == suffix[1:] for suffix in _UNCERTIFIABLE_SUFFIXES
    ):
        return None
    return name


def _aad(pairing_id: UUID) -> bytes:
    return b"pairing-proof-key|" + str(pairing_id).encode("ascii")


def seal_proof_key(key: bytes, pairing_id: UUID) -> bytes:
    """The derived key as stored: sealed with the master key and bound to its code's id,
    so a database dump alone cannot check a proof, and a key cannot be moved to another
    code's row."""
    return crypto.seal(crypto.master_key(), key, aad=_aad(pairing_id))


def open_proof_key(sealed: bytes, pairing_id: UUID) -> bytes:
    """Raises `crypto.DecryptionError` for another key, another code or changed bytes."""
    return crypto.open_sealed(crypto.master_key(), sealed, aad=_aad(pairing_id))

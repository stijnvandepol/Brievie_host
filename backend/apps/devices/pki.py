"""The private certificate authority that Caddy creates for `tls internal`.

The app pins this root by its SHA-256 (QR payload `ca_sha256`) and can download it
from `GET /server/ca-certificate`. With a public certificate (Let's Encrypt, a
tunnel) there is no such file and pinning is off.
"""

import hashlib
import ssl
from pathlib import Path

from django.conf import settings


def load_ca_certificate_pem() -> str | None:
    """The root certificate as PEM, or `None` when there is no private CA (yet).

    Caddy writes the file the first time it serves the site, so it can be missing
    right after installation.
    """
    path: str = settings.BRIEVIE_CA_CERT_PATH
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="ascii")
    except FileNotFoundError:
        return None


def certificate_sha256(pem: str) -> str:
    """Lower-case hex SHA-256 of the certificate's DER bytes (what clients pin)."""
    try:
        der = ssl.PEM_cert_to_DER_cert(pem.strip())
    except ValueError as exc:
        raise ValueError("Not a PEM certificate.") from exc
    return hashlib.sha256(der).hexdigest()


def ca_certificate_sha256() -> str | None:
    pem = load_ca_certificate_pem()
    return certificate_sha256(pem) if pem is not None else None

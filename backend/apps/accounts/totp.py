"""Time-based one-time codes (RFC 6238 over RFC 4226), with the standard library only.

The parameters every authenticator app supports: HMAC-SHA1, six digits, 30-second steps.
No Django import: pure functions, tested against the RFC's vectors.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
# The current step and one either side are accepted (clock drift of up to 30 seconds).
WINDOW = 1
# At set-up, a code this many steps away (but more than WINDOW) is reported as drift.
DRIFT_MAX = 10
SECRET_BYTES = 20


def new_secret() -> bytes:
    return secrets.token_bytes(SECRET_BYTES)


def step_of(now: datetime) -> int:
    """The number of whole 30-second steps since the Unix epoch."""
    return int(now.timestamp() // STEP_SECONDS)


def code_at(secret: bytes, step: int) -> str:
    """The code for one step: HOTP (RFC 4226) with dynamic truncation, zero-padded."""
    digest = hmac.new(secret, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def _clean(code: str) -> str | None:
    """The code without spaces, or None when it is not exactly six ASCII digits."""
    cleaned = code.replace(" ", "")
    if len(cleaned) != DIGITS or not all("0" <= ch <= "9" for ch in cleaned):
        return None
    return cleaned


def _matches(secret: bytes, code: str, step: int) -> bool:
    return hmac.compare_digest(code_at(secret, step).encode(), code.encode())


def match(secret: bytes, code: str, *, now: datetime, last_used_step: int) -> int | None:
    """The step in the window around `now` whose code this is and that is later than
    `last_used_step` (a used code never passes again), else None. Every step of the
    window is compared, in constant time, whatever matched first."""
    cleaned = _clean(code)
    if cleaned is None:
        return None
    current = step_of(now)
    found: int | None = None
    for step in range(current - WINDOW, current + WINDOW + 1):
        if _matches(secret, cleaned, step) and step > last_used_step and found is None:
            found = step
    return found


def drift_steps(secret: bytes, code: str, *, now: datetime) -> int | None:
    """How many steps away (2..DRIFT_MAX, either side) the code matches, for the set-up
    hint that a clock is off; None when it matches in the window or not at all."""
    cleaned = _clean(code)
    if cleaned is None:
        return None
    current = step_of(now)
    if any(_matches(secret, cleaned, current + d) for d in range(-WINDOW, WINDOW + 1)):
        return None
    for distance in range(WINDOW + 1, DRIFT_MAX + 1):
        if _matches(secret, cleaned, current - distance) or _matches(
            secret, cleaned, current + distance
        ):
            return distance
    return None


def _base32(secret: bytes) -> str:
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def grouped_secret(secret: bytes) -> str:
    """The secret as an app's "enter a key" field wants it, in groups of four."""
    text = _base32(secret)
    return " ".join(text[i : i + 4] for i in range(0, len(text), 4))


def otpauth_uri(secret: bytes, *, host: str, email: str) -> str:
    """The link a QR code carries for an authenticator app."""
    issuer = f"Brievie ({host})"
    label = f"{quote(issuer, safe='')}:{quote(email, safe='')}"
    query = urlencode(
        {
            "secret": _base32(secret),
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": str(DIGITS),
            "period": str(STEP_SECONDS),
        },
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{query}"

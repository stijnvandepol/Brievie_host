"""Recovery codes (two-step sign-in, S 2): ten one-time codes for a lost phone.

Ten characters of the pairing alphabet (50 bits), shown as XXXXX-XXXXX. Stored only as
an HMAC-SHA256 under a key derived from the master key: a leaked database cannot be
brute-forced without that key. No Django import.
"""

import hashlib
import hmac
import secrets

from apps.common.alphabet import CODE_ALPHABET

ALPHABET = CODE_ALPHABET
COUNT = 10
LENGTH = 10


def new_codes() -> list[str]:
    """Ten different codes, each as XXXXX-XXXXX."""
    codes: list[str] = []
    while len(codes) < COUNT:
        raw = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
        code = f"{raw[:5]}-{raw[5:]}"
        if code not in codes:
            codes.append(code)
    return codes


def normalise(code: str) -> str:
    """ "abcde-fghjk", " ABCDE FGHJK " and "ABCDEFGHJK" are the same code."""
    return "".join(ch for ch in code.upper() if ch not in "- ")


def code_hash(key: bytes, code: str) -> str:
    """The stored form of a code: 64 lower-case hex characters."""
    return hmac.new(key, normalise(code).encode("utf-8"), hashlib.sha256).hexdigest()

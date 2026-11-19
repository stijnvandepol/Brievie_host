"""One-time secrets in links (reset links, invitations): only their hash is stored."""

import hashlib
import secrets


def token_hash(token: str) -> str:
    """The SHA-256 hex of `token` without surrounding whitespace (a phone keyboard or a
    copied link adds some)."""
    return hashlib.sha256(token.strip().encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """A new token (43 URL-safe characters) and its hash. Show the token once; store the hash."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)

"""A limit on wrong passwords per account, whatever address they come from.

django-axes locks an email per client address, and `LOGIN_FAILURES` counts failures per
address. Somebody with many addresses (an IPv6 /64 holds billions) passes both. This adds
one count per account (security review 2026-11-20, identity M1):

- The first `FREE_FAILURES` wrong passwords cost nothing. After that each further attempt
  waits `delay(failures)` after the last wrong one: 15 seconds, doubling, at most
  `MAX_DELAY` (15 minutes). That bounds guessing to about a hundred tries a day per account.
- A browser that signed in to this account before carries a signed cookie
  (`KNOWN_BROWSER_COOKIE`) and is never slowed: an attacker who keeps an account slowed
  does not lock its owner out of the browsers they use. Axes still limits that browser
  per address.
- A sign-in clears the count; a count with no wrong password for a day starts again.
- Unknown addresses are counted the same way, so the answer says nothing about whether an
  account exists. Only a hash of the normalised email is stored.

The check, the password check and the count run under a transaction-level advisory lock
per account, so parallel requests cannot all pass before any failure is counted.
"""

import hashlib
from datetime import datetime, timedelta

from django.conf import settings
from django.core import signing
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import SignInFailures, normalise_email
from apps.common.errors import ApiError

FREE_FAILURES = 10
FIRST_DELAY = timedelta(seconds=15)
MAX_DELAY = timedelta(minutes=15)
# A count without a new wrong password for this long starts again.
FORGET_AFTER = timedelta(days=1)

KNOWN_BROWSER_COOKIE = "brievie_known"
_KNOWN_BROWSER_SALT = "apps.accounts.signin_limits.known_browser"
KNOWN_BROWSER_AGE = timedelta(days=180)
# The accounts one browser remembers (a shared computer at home).
_KNOWN_BROWSER_MAX = 5


def account_key(email: str) -> str:
    """The stored key of an account: a hash of the normalised email, never the email."""
    return hashlib.sha256(f"signin:{normalise_email(email)}".encode()).hexdigest()


def delay(failures: int) -> timedelta:
    """How long after the last wrong password the next attempt must wait."""
    if failures < FREE_FAILURES:
        return timedelta(0)
    steps = min(failures - FREE_FAILURES, 16)
    seconds = FIRST_DELAY.total_seconds() * (2**steps)
    return timedelta(seconds=min(seconds, MAX_DELAY.total_seconds()))


def _lock(key: str) -> None:
    """Serialise the attempts on one account until the request's transaction ends."""
    number = int.from_bytes(bytes.fromhex(key[:16]), "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [number])


def refuse_when_slowed(request: HttpRequest, email: str, *, now: datetime | None = None) -> None:
    """429 while this account must wait after its wrong passwords, unless this browser
    signed in to it before. Takes the account's lock for the rest of the request."""
    key = account_key(email)
    _lock(key)
    if is_known_browser(request, key):
        return
    row = SignInFailures.objects.filter(account_key=key).first()
    if row is None:
        return
    moment = now or timezone.now()
    if moment - row.last_failure_at > FORGET_AFTER:
        return
    wait_until = row.last_failure_at + delay(row.failures)
    if moment < wait_until:
        seconds = max(1, int((wait_until - moment).total_seconds()) + 1)
        raise ApiError(
            429,
            "too_many_attempts",
            _("Te veel pogingen. Probeer het later opnieuw."),
            headers={"Retry-After": str(seconds)},
        )


def record_failure(email: str, *, now: datetime | None = None) -> None:
    """Count a wrong password for this account (one atomic upsert)."""
    moment = now or timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO {SignInFailures._meta.db_table}
                (id, account_key, failures, last_failure_at, created_at, updated_at)
            VALUES (gen_random_uuid(), %(key)s, 1, %(now)s, %(now)s, %(now)s)
            ON CONFLICT (account_key) DO UPDATE SET
                failures = CASE
                    WHEN {SignInFailures._meta.db_table}.last_failure_at < %(stale)s THEN 1
                    ELSE {SignInFailures._meta.db_table}.failures + 1
                END,
                last_failure_at = %(now)s,
                updated_at = %(now)s
            """,  # noqa: S608 - the table name is ours; values are parameters
            {"key": account_key(email), "now": moment, "stale": moment - FORGET_AFTER},
        )
        # Old counts of addresses nobody tried for a day: the table stays small.
        cursor.execute(
            f"DELETE FROM {SignInFailures._meta.db_table} WHERE last_failure_at < %s",  # noqa: S608
            [moment - FORGET_AFTER],
        )


def clear(email: str) -> None:
    """A sign-in with the right password: the account's count starts again."""
    SignInFailures.objects.filter(account_key=account_key(email)).delete()


def _known_keys(request: HttpRequest) -> list[str]:
    raw = request.COOKIES.get(KNOWN_BROWSER_COOKIE)
    if not raw:
        return []
    try:
        value = signing.loads(raw, salt=_KNOWN_BROWSER_SALT, max_age=KNOWN_BROWSER_AGE)
    except signing.BadSignature:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)][:_KNOWN_BROWSER_MAX]


def is_known_browser(request: HttpRequest, key: str) -> bool:
    return key in _known_keys(request)


def remember_browser(request: HttpRequest, response: HttpResponse, email: str) -> None:
    """After a sign-in: this browser is known for this account (signed, HttpOnly)."""
    key = account_key(email)
    keys = [key, *(k for k in _known_keys(request) if k != key)][:_KNOWN_BROWSER_MAX]
    response.set_cookie(
        KNOWN_BROWSER_COOKIE,
        signing.dumps(keys, salt=_KNOWN_BROWSER_SALT),
        max_age=int(KNOWN_BROWSER_AGE.total_seconds()),
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="Lax",
    )

"""Web sessions (D 7.2): the one place a sign-in starts one, and how they are ended.

Every signed-in session has a `UserSession` row; `UserSessionMiddleware` signs out a
session without one and enforces the idle and absolute limits of `session_policy`.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User, UserSession
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.net import valid_client_ip

# Two authentication backends are configured (axes first); a session must name the one
# that holds the user, also for a user object that did not come from `authenticate`.
MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"


@dataclass(frozen=True)
class SessionPolicy:
    idle: timedelta
    maximum: timedelta


def session_policy(method: str) -> SessionPolicy:
    """How long a session of this sign-in method lives (part C caps SSO here)."""
    return SessionPolicy(
        idle=timedelta(days=settings.BRIEVIE_SESSION_IDLE_DAYS),
        maximum=timedelta(days=settings.BRIEVIE_SESSION_MAX_DAYS),
    )


def expiry_of(row: UserSession, now: datetime) -> datetime:
    """The earlier of the idle and the absolute limit, counted from `now`."""
    policy = session_policy(row.method)
    return min(now + policy.idle, row.created_at + policy.maximum)


def is_past_its_limits(row: UserSession, now: datetime) -> bool:
    policy = session_policy(row.method)
    return now - row.created_at > policy.maximum or now - row.last_used_at > policy.idle


def record_session(
    request: HttpRequest, user: User, *, method: str, via: str = UserSession.Via.PASSWORD
) -> UserSession:
    """Record the request's session as a sign-in of `user` and set its expiry.

    `login()` flushes the session when another user was signed in, which leaves no key
    until the session is saved; so save it first. A second call for the same key (the
    test run's `user_logged_in` receiver, then `start_session`) updates the same row.
    """
    if request.session.session_key is None:
        request.session.save()
    key = request.session.session_key
    if key is None:
        raise RuntimeError("The session has no key after saving.")
    now = timezone.now()
    row, created = UserSession.objects.update_or_create(
        session_key=key,
        defaults={
            "user": user,
            "method": method,
            "created_via": via,
            # A sign-in starts the absolute limit again, also when Django kept the key.
            "created_at": now,
            "last_used_at": now,
            "client_ip": valid_client_ip(request),
            "browser": describe_user_agent(str(request.META.get("HTTP_USER_AGENT", ""))),
        },
    )
    if created and row.last_used_at != row.created_at:
        # On insert `created_at` is set by auto_now_add, a moment after `now`: a new
        # session would have been used before it started.
        UserSession.objects.filter(pk=row.pk).update(last_used_at=row.created_at)
        row.last_used_at = row.created_at
    request.session.set_expiry(expiry_of(row, now))
    return row


def start_session(
    request: HttpRequest,
    user: User,
    *,
    method: str = UserSession.Method.PASSWORD,
    via: str,
) -> None:
    """Sign `user` in on this request: the only caller of Django's `login`.

    `via` is how: password, setup, invitation or reset. A session that belonged to
    somebody else before is ended (Django flushed it; its record goes too).
    """
    old_key = request.session.session_key
    with transaction.atomic():
        django_login(request, user, backend=MODEL_BACKEND)
        record_session(request, user, method=method, via=via)
        if old_key and old_key != request.session.session_key:
            UserSession.objects.filter(session_key=old_key).delete()
        record(
            AuditCode.SIGN_IN_SUCCEEDED,
            actor=AuditActor.for_user(user, client_ip=valid_client_ip(request)),
            target=AuditTarget(kind=TargetKind.USER, id=user.id, name=user.name),
            values={"via": via},
        )


def rekey_session(request: HttpRequest, old_key: str) -> None:
    """Move the record to the session's new key after Django cycled it
    (`update_session_auth_hash` does), so the middleware does not sign the user out."""
    with transaction.atomic():
        UserSession.objects.filter(session_key=old_key).update(
            session_key=request.session.session_key
        )


def end_sessions(
    user: User, *, except_key: str | None = None, current: HttpRequest | None = None
) -> int:
    """End `user`'s web sessions (both rows), except `except_key`; return how many.

    When `current`'s own session is among them it is ended with `flush()`: deleting
    Django's row under a session this request changed makes `SessionMiddleware` answer
    with an HTML 400 (`SessionInterrupted`).
    """
    current_key = current.session.session_key if current is not None else None
    with transaction.atomic():
        rows = UserSession.objects.select_for_update().filter(user=user)
        if except_key is not None:
            rows = rows.exclude(session_key=except_key)
        keys = list(rows.values_list("session_key", flat=True))
        UserSession.objects.filter(session_key__in=keys).delete()
        Session.objects.filter(session_key__in=[k for k in keys if k != current_key]).delete()
    if current is not None and current_key in keys:
        current.session.flush()
    return len(keys)


def end_current_session(request: HttpRequest) -> None:
    """End the caller's own session: its record, then Django's session by `flush()`."""
    with transaction.atomic():
        UserSession.objects.filter(session_key=request.session.session_key).delete()
    request.session.flush()


# How long after signing in, or typing the password again, a session may do what needs a
# recent proof of who is at the browser (creating a pairing code). Longer than a pairing
# code lives (ten minutes), so "Nieuwe code" right after accepting an invitation works.
REAUTHENTICATION_WINDOW = timedelta(minutes=15)


def recently_authenticated(request: HttpRequest, *, now: datetime | None = None) -> bool:
    """Whether this web session signed in, or had its password typed again, within
    `REAUTHENTICATION_WINDOW`. False without a session record (a device token)."""
    row = UserSession.objects.filter(session_key=request.session.session_key).first()
    if row is None:
        return False
    proved = max(row.created_at, row.reauthenticated_at or row.created_at)
    return (now or timezone.now()) - proved <= REAUTHENTICATION_WINDOW


def mark_reauthenticated(request: HttpRequest) -> None:
    """Note that the password was just typed again in this session."""
    with transaction.atomic():
        UserSession.objects.filter(session_key=request.session.session_key).update(
            reauthenticated_at=timezone.now()
        )


def touch_session(row: UserSession, request: HttpRequest, *, now: datetime) -> None:
    """Note a use of the session and move its expiry (at most once a minute, see the
    middleware), so Django's own `expire_date` and the cookie enforce both limits."""
    with transaction.atomic():
        UserSession.objects.filter(pk=row.pk).update(
            last_used_at=now, client_ip=valid_client_ip(request)
        )
    request.session.set_expiry(expiry_of(row, now))


# Order matters: Edge and the iOS browsers also say "Chrome" or "Safari".
_BROWSERS: tuple[tuple[str, str], ...] = (
    (r"\bEdg(e|A|iOS)?/", "Edge"),
    (r"\bOPR/|\bOpera\b", "Opera"),
    (r"\bFirefox/|\bFxiOS/", "Firefox"),
    (r"\bCriOS/|\bChrome/", "Chrome"),
    (r"\bSafari/", "Safari"),
)
_SYSTEMS: tuple[tuple[str, str], ...] = (
    (r"\biPhone\b", "iPhone"),
    (r"\biPad\b", "iPad"),
    (r"\bAndroid\b", "Android"),
    (r"\bWindows\b", "Windows"),
    (r"\bMacintosh\b|\bMac OS X\b", "Mac"),
    (r"\bCrOS\b", "ChromeOS"),
    (r"\bLinux\b", "Linux"),
)
MAX_USER_AGENT = 512


def _first_match(patterns: tuple[tuple[str, str], ...], text: str) -> str:
    return next((name for pattern, name in patterns if re.search(pattern, text)), "")


def describe_user_agent(user_agent: str) -> str:
    """A short description of the browser, such as "Firefox op Windows"."""
    text = user_agent[:MAX_USER_AGENT]
    browser = _first_match(_BROWSERS, text)
    system = _first_match(_SYSTEMS, text)
    if not browser:
        return _("Onbekende browser")
    if not system:
        return browser
    return _("%(browser)s op %(system)s") % {"browser": browser, "system": system}

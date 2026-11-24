"""The one place that decides whether a sign-in needs a second step (two-step sign-in, S 3).

Every way in that proved who somebody is (the password, the setup, accepting an
invitation, completing a reset) calls `begin_sign_in`. It signs in at once when no
second step is needed; otherwise it leaves a pending sign-in in the browser's anonymous
session and answers which step follows: `code` (the account has a factor) or `setup` (a
rule asks for one and the grace period is over).

A pending sign-in is no session: the browser stays signed out (every normal endpoint
answers 401) until `complete_sign_in`. It holds no secret: the account, how it began, the
step, when, the wrong codes so far, the factor it must be checked against and the
account's session hash (a password changed since ends it). It lives `PENDING_LIFETIME`
(five minutes, also for setting up a factor: install the app first, or sign in again);
clearing, removing or replacing the factor ends it too.

Only this module calls `sessions.start_session`, which itself refuses a sign-in without
the second step where one is needed.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from apps.accounts.models import AccountNotice, SecondFactor, User, UserSession
from apps.accounts.notices import notify
from apps.accounts.second_factor import (
    SecondFactorPassed,
    grace_day,
    passed_by_sso,
    passed_without_factor,
    sign_in_expired,
    verify_code,
    verify_recovery_code,
)
from apps.accounts.second_factor_policy import in_grace, note_required_since, requirement_for
from apps.accounts.sessions import start_session
from apps.common.errors import ApiError
from apps.common.net import valid_client_ip

PENDING_KEY = "brievie_pending_sign_in"
PENDING_LIFETIME = timedelta(minutes=5)
PENDING_MAX_FAILURES = 5

Step = Literal["code", "setup"]


@dataclass(frozen=True)
class SignedIn:
    user: User


@dataclass(frozen=True)
class StepNeeded:
    step: Step
    expires_in_seconds: int


SignInResult = SignedIn | StepNeeded


@dataclass(frozen=True)
class Pending:
    user_id: UUID
    via: str
    method: str
    step: Step
    started_at: datetime
    failures: int
    # The factor the code is checked against ("" for the step `setup`).
    factor_id: str
    auth_hash: str


def _lifetime(step: Step) -> timedelta:
    return PENDING_LIFETIME


def _confirmed_factor_id(user: User) -> UUID | None:
    return (
        SecondFactor.objects.filter(user_id=user.id, confirmed_at__isnull=False)
        .values_list("id", flat=True)
        .first()
    )


def begin_sign_in(
    request: HttpRequest,
    user: User,
    *,
    via: str,
    method: str = UserSession.Method.PASSWORD,
    now: datetime | None = None,
) -> SignInResult:
    """Sign `user` in, or leave the pending step that must come first."""
    moment = now or timezone.now()
    if method == UserSession.Method.SSO:
        # The identity provider does the second step (S 8).
        start_session(request, user, method=method, via=via, second_factor=passed_by_sso(user))
        return SignedIn(user)
    note_required_since(user, now=moment)
    factor_id = _confirmed_factor_id(user)
    if factor_id is not None:
        return _leave_pending(
            request, user, via=via, method=method, step="code", factor_id=factor_id, now=moment
        )
    requirement = requirement_for(user, now=moment)
    if requirement.required and not in_grace(requirement, now=moment):
        return _leave_pending(
            request, user, via=via, method=method, step="setup", factor_id=None, now=moment
        )
    start_session(request, user, method=method, via=via, second_factor=passed_without_factor(user))
    if requirement.required and requirement.grace_until is not None:
        _remind_of_the_grace(user, requirement.grace_until)
    return SignedIn(user)


def _remind_of_the_grace(user: User, until: datetime) -> None:
    """One notice while the grace period runs, not one per sign-in."""
    open_one = AccountNotice.objects.filter(
        user=user, code=AccountNotice.Code.SECOND_FACTOR_REQUIRED, seen_at__isnull=True
    ).exists()
    if not open_one:
        notify(
            user,
            AccountNotice.Code.SECOND_FACTOR_REQUIRED,
            actor_name="",
            values={"until": grace_day(until)},
        )


def _leave_pending(
    request: HttpRequest,
    user: User,
    *,
    via: str,
    method: str,
    step: Step,
    factor_id: UUID | None,
    now: datetime,
) -> StepNeeded:
    """Write the pending sign-in into a fresh anonymous session: whatever the browser had
    (another account's session, an older pending sign-in) ends, and the key is new."""
    old_key = request.session.session_key
    if request.session.get(SESSION_KEY) is not None and old_key is not None:
        with transaction.atomic():
            UserSession.objects.filter(session_key=old_key).delete()
    request.session.flush()
    request.user = AnonymousUser()
    lifetime = _lifetime(step)
    request.session[PENDING_KEY] = {
        "user_id": str(user.id),
        "via": via,
        "method": method,
        "step": step,
        "started_at": now.isoformat(),
        "failures": 0,
        "factor_id": str(factor_id) if factor_id is not None else "",
        "auth_hash": user.get_session_auth_hash(),
    }
    request.session.set_expiry(int(lifetime.total_seconds()))
    # A new key now, not only when the response is written.
    request.session.save()
    return StepNeeded(step=step, expires_in_seconds=int(lifetime.total_seconds()))


def _read(raw: object) -> Pending | None:
    if not isinstance(raw, dict):
        return None
    try:
        step = raw["step"]
        if step not in ("code", "setup"):
            return None
        return Pending(
            user_id=UUID(str(raw["user_id"])),
            via=str(raw["via"]),
            method=str(raw["method"]),
            step=step,
            started_at=datetime.fromisoformat(str(raw["started_at"])),
            failures=int(raw["failures"]),
            factor_id=str(raw["factor_id"]),
            auth_hash=str(raw["auth_hash"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def drop_pending(request: HttpRequest) -> None:
    request.session.pop(PENDING_KEY, None)


def pending(request: HttpRequest, *, now: datetime | None = None) -> Pending | None:
    """The browser's pending sign-in, or None (an expired one is removed)."""
    if request.session.get(SESSION_KEY) is not None:
        return None
    found = _read(request.session.get(PENDING_KEY))
    if found is None:
        drop_pending(request)
        return None
    if (now or timezone.now()) - found.started_at > _lifetime(found.step):
        drop_pending(request)
        return None
    return found


def pending_user(request: HttpRequest, step: Step, *, now: datetime | None = None) -> User | None:
    """The account of the browser's pending sign-in at `step`, while it is still valid:
    the account is active and its password unchanged since the sign-in began."""
    found = pending(request, now=now)
    if found is None or found.step != step:
        return None
    user = User.objects.filter(id=found.user_id, is_active=True).first()
    if user is None or not constant_time_compare(user.get_session_auth_hash(), found.auth_hash):
        drop_pending(request)
        return None
    return user


def complete_sign_in(request: HttpRequest, passed: SecondFactorPassed) -> User:
    """Sign in the pending account now that the second step is done."""
    found = pending(request)
    if found is None or found.user_id != passed.user_id:
        raise sign_in_expired()
    user = User.objects.get(id=found.user_id)
    drop_pending(request)
    start_session(request, user, method=found.method, via=found.via, second_factor=passed)
    return user


def verify_sign_in_code(
    request: HttpRequest, *, code: str, recovery_code: str, now: datetime | None = None
) -> User:
    """The second step of a pending sign-in: a code from the app or a recovery code.

    401 `sign_in_expired` without a valid pending step (expired, signed in elsewhere, the
    password changed, the factor cleared or replaced, five wrong codes in this sign-in);
    401 `second_factor_invalid` for a wrong code; 429 `second_factor_locked` while the
    account is locked.
    """
    moment = now or timezone.now()
    user = pending_user(request, "code", now=moment)
    found = pending(request, now=moment)
    if user is None or found is None:
        raise sign_in_expired()
    factor_id = UUID(found.factor_id) if found.factor_id else None
    if factor_id is None:
        drop_pending(request)
        raise sign_in_expired()
    client_ip = valid_client_ip(request)
    try:
        if recovery_code:
            passed = verify_recovery_code(
                user, recovery_code, now=moment, client_ip=client_ip, factor_id=factor_id
            )
        else:
            passed = verify_code(user, code, now=moment, client_ip=client_ip, factor_id=factor_id)
    except ApiError as error:
        _count_pending_failure(request, found, error)
        raise
    return complete_sign_in(request, passed)


def _count_pending_failure(request: HttpRequest, found: Pending, error: ApiError) -> None:
    """Five wrong codes in one sign-in, or the account's lock, end the pending step: back
    to the password, which axes counts."""
    if error.code == "sign_in_expired":
        drop_pending(request)
        return
    if error.code not in ("second_factor_invalid", "second_factor_locked"):
        return
    failures = found.failures + 1
    if error.code == "second_factor_locked" or failures >= PENDING_MAX_FAILURES:
        drop_pending(request)
        return
    raw = request.session.get(PENDING_KEY)
    if isinstance(raw, dict):
        request.session[PENDING_KEY] = {**raw, "failures": failures}

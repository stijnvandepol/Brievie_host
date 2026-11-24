"""An account's second factor (two-step sign-in, S 1-7): set it up, check a code or a
recovery code, renew the recovery codes, remove it, clear it.

The secret is sealed under a key derived from the master key and bound to the account;
recovery codes are stored as HMACs under another derived key. Neither is ever logged,
audited, put in a notice or an error, or answered again after the response that shows
it.

Checking a code locks the factor's row: a code is accepted for one step only once
(`last_used_step`), also by two requests at the same moment. Wrong codes count per
account: every `LOCK_AFTER` since the last right code (forgotten only after `FORGET_AFTER`
without a wrong one) lock the second step, for `FIRST_LOCK`,
doubling with each further lock up to `MAX_LOCK`. A failed check keeps its count: the
counter's block ends before the error is raised (the error answer does not roll back the
request).

Lock order (it extends the one of `apps.workspaces.members`): ... -> the account's user
row (`FOR NO KEY UPDATE`) -> its `SecondFactor` -> its recovery codes -> its sessions,
devices and reset links.

`SecondFactorPassed` is made only here: it is what `sessions.start_session` needs before
it signs anybody in.
"""

import hmac
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal
from uuid import UUID

from django.db import transaction
from django.http import HttpRequest
from django.utils import formats, timezone
from django.utils.translation import gettext as _

from apps.accounts import recovery_codes, totp
from apps.accounts.models import AccountNotice, RecoveryCode, SecondFactor, User
from apps.accounts.notices import NOTICE_ZONE, notify
from apps.accounts.second_factor_policy import requirement_for
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, AuditValue, record
from apps.common import crypto
from apps.common.errors import ApiError
from apps.devices.models import Device

logger = logging.getLogger(__name__)

LOCK_AFTER = 5
# Wrong codes are forgotten only after this long without one (or by a right code): no
# window that starts the count again (security review of PR #52, point 1).
FORGET_AFTER = timedelta(hours=24)
FIRST_LOCK = timedelta(minutes=15)
MAX_LOCK = timedelta(hours=24)
# A set-up that was started but never confirmed is removed after this long.
UNCONFIRMED_LIFETIME = timedelta(hours=1)
# At this many recovery codes left or fewer, the user is asked to make new ones.
FEW_RECOVERY_CODES = 2

Method = Literal["totp", "recovery_code", "none", "sso"]

# Only this module holds it: a `SecondFactorPassed` made anywhere else is refused.
_ISSUER = object()


@dataclass(frozen=True)
class SecondFactorPassed:
    """Proof that a sign-in of `user_id` passed the second step (or needs none)."""

    user_id: UUID
    method: Method
    issuer: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.issuer is not _ISSUER:
            raise TypeError("A SecondFactorPassed is made only by apps.accounts.second_factor.")


def _passed(user: User, method: Method) -> SecondFactorPassed:
    return SecondFactorPassed(user_id=user.id, method=method, issuer=_ISSUER)


def passed_without_factor(user: User) -> SecondFactorPassed:
    """For a sign-in that needs no second step: `start_session` checks again that the
    account has no factor and is not past its grace period."""
    return _passed(user, "none")


def passed_by_sso(user: User) -> SecondFactorPassed:
    """A sign-in through the organisation's identity provider, which does its own second
    step (S 8)."""
    return _passed(user, "sso")


def lock_length(lockouts: int) -> timedelta:
    """How long the `lockouts`-th lock in a row lasts: 15 minutes, doubling, at most a day."""
    steps = max(0, min(lockouts - 1, 16))
    length: timedelta = FIRST_LOCK * (2**steps)
    return min(length, MAX_LOCK)


# --- Secrets --------------------------------------------------------------------------


def _secret_key() -> bytes:
    return crypto.derive_second_factor_key(crypto.master_key())


def _recovery_key() -> bytes:
    return crypto.derive_recovery_code_key(crypto.master_key())


def seal_secret(user_id: UUID, secret: bytes) -> bytes:
    return crypto.seal(_secret_key(), secret, aad=crypto.second_factor_aad(user_id))


def open_secret(user_id: UUID, sealed: bytes | memoryview) -> bytes:
    return crypto.open_sealed(_secret_key(), bytes(sealed), aad=crypto.second_factor_aad(user_id))


def _store_new_recovery_codes(user: User) -> list[str]:
    """Replace the account's recovery codes with ten new ones; answer them (shown once)."""
    RecoveryCode.objects.filter(user=user).delete()
    codes = recovery_codes.new_codes()
    key = _recovery_key()
    RecoveryCode.objects.bulk_create(
        RecoveryCode(user=user, code_hash=recovery_codes.code_hash(key, code)) for code in codes
    )
    return codes


def recovery_codes_left(user: User) -> int:
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


# --- Errors ---------------------------------------------------------------------------


def _invalid(status: int) -> ApiError:
    """One answer for a wrong code, a wrong recovery code and a used one: 401 at sign-in,
    400 in a session (a 401 there would read as signed out)."""
    return ApiError(status, "second_factor_invalid", _("Code klopt niet. Probeer het opnieuw."))


def _locked(until: datetime, now: datetime) -> ApiError:
    left = max(1.0, (until - now).total_seconds())
    seconds = int(left) + 1
    minutes = math.ceil(left / 60)
    if minutes < 120:
        message = _("Te vaak een verkeerde code. Probeer het over %(minutes)s minuten opnieuw.") % {
            "minutes": minutes
        }
    else:
        message = _("Te vaak een verkeerde code. Probeer het over %(hours)s uur opnieuw.") % {
            "hours": math.ceil(minutes / 60)
        }
    return ApiError(429, "second_factor_locked", message, headers={"Retry-After": str(seconds)})


def sign_in_expired() -> ApiError:
    return ApiError(401, "sign_in_expired", _("Je bent te lang bezig geweest. Log opnieuw in."))


def _not_set() -> ApiError:
    return ApiError(
        409, "second_factor_not_set", _("Tweestapsverificatie staat niet aan voor dit account.")
    )


# --- Set-up ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SetupSecret:
    # The secret in groups of four, for an app's "enter a key" field.
    secret_text: str
    otpauth_uri: str


@dataclass(frozen=True)
class ConfirmedSetup:
    recovery_codes: list[str]
    passed: SecondFactorPassed


def _target(user: User) -> AuditTarget:
    return AuditTarget(kind=TargetKind.USER, id=user.id, name=user.name)


def begin_setup(user: User, *, host: str) -> SetupSecret:
    """A new secret for an account without a confirmed factor, shown once; an earlier
    unfinished set-up is replaced. 409 `second_factor_exists` when one is confirmed (a new
    phone goes through `begin_replacement`)."""
    secret = totp.new_secret()
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        factor = SecondFactor.objects.select_for_update().filter(user=user).first()
        if factor is not None and factor.confirmed_at is not None:
            raise ApiError(
                409,
                "second_factor_exists",
                _("Tweestapsverificatie staat al aan. Kies Nieuwe telefoon om te wisselen."),
            )
        if factor is not None:
            factor.delete()
        SecondFactor.objects.create(user=user, secret_sealed=seal_secret(user.id, secret))
    return SetupSecret(
        secret_text=totp.grouped_secret(secret),
        otpauth_uri=totp.otpauth_uri(secret, host=host, email=user.email),
    )


def _drift_error() -> ApiError:
    return ApiError(
        409,
        "second_factor_clock_drift",
        _(
            "De klok van je telefoon of van de server loopt niet gelijk. Zet op je telefoon "
            "de tijd op automatisch."
        ),
    )


def confirm_setup(user: User, code: str, *, now: datetime, client_ip: str | None) -> ConfirmedSetup:
    """Confirm the set-up with a code from the app: the factor is on, ten new recovery
    codes are made and answered (shown once). 400 `second_factor_invalid` for a wrong
    code, 409 `second_factor_clock_drift` when it matches only 2-10 steps away, 409
    `second_factor_setup_missing` without a set-up begun."""
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        factor = SecondFactor.objects.select_for_update().filter(user=user).first()
        if factor is None or factor.confirmed_at is not None:
            raise ApiError(
                409,
                "second_factor_setup_missing",
                _("Begin opnieuw met het aanzetten van tweestapsverificatie."),
            )
        secret = open_secret(user.id, factor.secret_sealed)
        step = totp.match(secret, code, now=now, last_used_step=0)
        if step is None:
            if totp.drift_steps(secret, code, now=now) is not None:
                raise _drift_error()
            raise _invalid(400)
        factor.confirmed_at = now
        factor.last_used_step = step
        _reset_counters(factor)
        factor.save()
        codes = _store_new_recovery_codes(user)
        record(
            AuditCode.SECOND_FACTOR_ADDED,
            actor=AuditActor.for_user(user, client_ip=client_ip),
            target=_target(user),
        )
    return ConfirmedSetup(recovery_codes=codes, passed=_passed(user, "totp"))


# --- Checking a code ------------------------------------------------------------------


class _Outcome(Enum):
    PASSED = "passed"
    WRONG = "wrong"
    LOCKED = "locked"
    MISSING = "missing"


def _reset_counters(factor: SecondFactor) -> None:
    factor.failed_count = 0
    factor.last_failed_at = None
    factor.locked_until = None
    factor.lockouts = 0


def _tell_the_account(user: User) -> None:
    """Somebody had the password but not the code: one open notice at a time."""
    open_one = AccountNotice.objects.filter(
        user=user, code=AccountNotice.Code.SECOND_FACTOR_LOCKED, seen_at__isnull=True
    ).exists()
    if not open_one:
        notify(user, AccountNotice.Code.SECOND_FACTOR_LOCKED, actor_name="")


def _count_failure(
    factor: SecondFactor, user: User, *, now: datetime, actor: AuditActor, signing_in: bool
) -> None:
    """One more wrong code for the account; every fifth since the last right code locks
    it. A wrong code at sign-in proves somebody has the password: the account is told."""
    if factor.last_failed_at is not None and now - factor.last_failed_at > FORGET_AFTER:
        factor.failed_count = 0
    factor.failed_count += 1
    factor.last_failed_at = now
    if signing_in:
        _tell_the_account(user)
    if factor.failed_count >= LOCK_AFTER:
        factor.lockouts += 1
        factor.locked_until = now + lock_length(factor.lockouts)
        factor.failed_count = 0
        record(
            AuditCode.SIGN_IN_LOCKED_OUT,
            actor=actor,
            target=_target(user),
            values={"via": "second_factor"},
        )
        _tell_the_account(user)
    factor.save(
        update_fields=[
            "failed_count",
            "last_failed_at",
            "locked_until",
            "lockouts",
            "updated_at",
        ]
    )


def _locked_factor(user: User, factor_id: UUID | None) -> SecondFactor | None:
    """The account's confirmed factor, locked; None when it has none, or when it is not
    the one `factor_id` names (a sign-in that began before it was cleared or replaced)."""
    factor = (
        SecondFactor.objects.select_for_update()
        .filter(user=user, confirmed_at__isnull=False)
        .first()
    )
    if factor is None or (factor_id is not None and factor.id != factor_id):
        return None
    return factor


class _Check:
    """What one attempt compares; on a match it updates the factor (not yet saved)."""

    def __call__(self, factor: SecondFactor, now: datetime) -> bool:
        raise NotImplementedError


class _TotpCheck(_Check):
    def __init__(self, user: User, code: str) -> None:
        self.user = user
        self.code = code

    def __call__(self, factor: SecondFactor, now: datetime) -> bool:
        secret = open_secret(self.user.id, factor.secret_sealed)
        step = totp.match(secret, self.code, now=now, last_used_step=factor.last_used_step)
        if step is None:
            return False
        factor.last_used_step = step
        return True


class _RecoveryCheck(_Check):
    def __init__(self, user: User, code: str) -> None:
        self.user = user
        self.code = code
        self.left = 0

    def __call__(self, factor: SecondFactor, now: datetime) -> bool:
        wanted = recovery_codes.code_hash(_recovery_key(), self.code)
        unused = list(
            RecoveryCode.objects.select_for_update().filter(user=self.user, used_at__isnull=True)
        )
        # Every unused code is compared, in constant time, whichever matches.
        found: RecoveryCode | None = None
        for candidate in unused:
            if hmac.compare_digest(candidate.code_hash.encode(), wanted.encode()) and found is None:
                found = candidate
        if found is None:
            return False
        found.used_at = now
        found.save(update_fields=["used_at", "updated_at"])
        self.left = len(unused) - 1
        return True


@dataclass(frozen=True)
class _Attempt:
    outcome: _Outcome
    locked_until: datetime | None = None


def _attempt(
    user: User,
    *,
    now: datetime,
    client_ip: str | None,
    factor_id: UUID | None,
    step: Literal["second_factor", "recovery_code"],
    check: _Check,
) -> _Attempt:
    """Run one check under the factor's lock and keep what it counted."""
    actor = AuditActor.for_user(user, client_ip=client_ip)
    with transaction.atomic():
        factor = _locked_factor(user, factor_id)
        if factor is None:
            return _Attempt(_Outcome.MISSING)
        if factor.locked_until is not None and now < factor.locked_until:
            # Still counted in the log; the lock holds whatever was typed.
            record(
                AuditCode.SIGN_IN_FAILED, actor=actor, target=_target(user), values={"step": step}
            )
            return _Attempt(_Outcome.LOCKED, locked_until=factor.locked_until)
        if check(factor, now):
            _reset_counters(factor)
            factor.save()
            return _Attempt(_Outcome.PASSED)
        record(AuditCode.SIGN_IN_FAILED, actor=actor, target=_target(user), values={"step": step})
        _count_failure(factor, user, now=now, actor=actor, signing_in=factor_id is not None)
        if factor.locked_until is not None and now < factor.locked_until:
            return _Attempt(_Outcome.LOCKED, locked_until=factor.locked_until)
        return _Attempt(_Outcome.WRONG)


def _raise_for(attempt: _Attempt, *, now: datetime, signing_in: bool) -> None:
    if attempt.outcome is _Outcome.MISSING:
        raise sign_in_expired() if signing_in else _not_set()
    if attempt.outcome is _Outcome.LOCKED and attempt.locked_until is not None:
        raise _locked(attempt.locked_until, now)
    if attempt.outcome is not _Outcome.PASSED:
        raise _invalid(401 if signing_in else 400)


def verify_code(
    user: User,
    code: str,
    *,
    now: datetime,
    client_ip: str | None,
    factor_id: UUID | None = None,
) -> SecondFactorPassed:
    """A code from the app. `factor_id` is given by a sign-in (the factor it began with).
    `second_factor_invalid` for a wrong or used code (401 at sign-in, 400 otherwise), 429
    `second_factor_locked` while the account is locked (also for the right code); without
    a confirmed factor, or another one than `factor_id`, 401 `sign_in_expired` at sign-in
    and 409 `second_factor_not_set` otherwise."""
    attempt = _attempt(
        user,
        now=now,
        client_ip=client_ip,
        factor_id=factor_id,
        step="second_factor",
        check=_TotpCheck(user, code),
    )
    _raise_for(attempt, now=now, signing_in=factor_id is not None)
    return _passed(user, "totp")


def verify_recovery_code(
    user: User,
    code: str,
    *,
    now: datetime,
    client_ip: str | None,
    factor_id: UUID | None = None,
) -> SecondFactorPassed:
    """A recovery code: used up on success (the log says how many are left, and so does a
    notice to the account). Answers as `verify_code`."""
    check = _RecoveryCheck(user, code)
    attempt = _attempt(
        user,
        now=now,
        client_ip=client_ip,
        factor_id=factor_id,
        step="recovery_code",
        check=check,
    )
    _raise_for(attempt, now=now, signing_in=factor_id is not None)
    with transaction.atomic():
        record(
            AuditCode.SECOND_FACTOR_RECOVERY_USED,
            actor=AuditActor.for_user(user, client_ip=client_ip),
            target=_target(user),
            values={"left": check.left},
        )
        notify(
            user,
            AccountNotice.Code.SECOND_FACTOR_RECOVERY_USED,
            actor_name="",
            values={"count": str(check.left)},
        )
    return _passed(user, "recovery_code")


def renew_recovery_codes(
    user: User, code: str, *, now: datetime, client_ip: str | None
) -> list[str]:
    """Ten new recovery codes after a code from the app; the old ones stop."""
    verify_code(user, code, now=now, client_ip=client_ip)
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        if (
            SecondFactor.objects.select_for_update()
            .filter(user=user, confirmed_at__isnull=False)
            .first()
            is None
        ):
            raise _not_set()
        codes = _store_new_recovery_codes(user)
        record(
            AuditCode.SECOND_FACTOR_RECOVERY_RENEWED,
            actor=AuditActor.for_user(user, client_ip=client_ip),
            target=_target(user),
        )
    return codes


# --- Removing and clearing ------------------------------------------------------------


def remove_factor(
    request: HttpRequest, user: User, *, password: str, code: str, now: datetime
) -> None:
    """The account's own removal: its password (counted like a sign-in) and a code. 409
    `second_factor_required` while a rule asks it (a new phone is the way then). Every
    other session of the account ends: what it signed in with is gone."""
    from apps.accounts.services import check_own_password
    from apps.accounts.sessions import end_sessions
    from apps.common.net import valid_client_ip

    if requirement_for(user, now=now).required:
        raise ApiError(
            409,
            "second_factor_required",
            _(
                "Tweestapsverificatie is verplicht. Je kunt het niet uitzetten, wel naar een "
                "nieuwe telefoon zetten."
            ),
        )
    check_own_password(request, user, password, via="second_factor_remove")
    client_ip = valid_client_ip(request)
    verify_code(user, code, now=now, client_ip=client_ip)
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        deleted, _counts = SecondFactor.objects.filter(user=user).delete()
        if not deleted:
            raise _not_set()
        RecoveryCode.objects.filter(user=user).delete()
        actor = AuditActor.for_user(user, client_ip=client_ip)
        record(AuditCode.SECOND_FACTOR_REMOVED, actor=actor, target=_target(user))
        ended = end_sessions(user, except_key=request.session.session_key)
        if ended:
            record(
                AuditCode.SESSION_ENDED,
                actor=actor,
                target=_target(user),
                values={"count": ended, "reason": "second_factor_removed"},
            )


def clear_factor(
    user: User,
    *,
    revoke_devices: bool,
    via: Literal["admin", "command"],
    actor: AuditActor,
    actor_name: str,
    current: HttpRequest | None = None,
    workspace_id: UUID | None = None,
) -> int:
    """Clear a lost factor (an admin, or the server's command): the factor and its
    recovery codes go, every session of the account ends, its paired devices are unpaired
    when asked, the log records it and the account gets a notice. Answers how many devices
    were unpaired. 404 `second_factor_not_set` when the account has none.

    Must run inside the caller's transaction after its authority check, which has locked
    the account's row (the lock order above)."""
    from apps.accounts.sessions import end_sessions
    from apps.devices.services import revoke_device

    with transaction.atomic():
        User.objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        factor = SecondFactor.objects.select_for_update().filter(user=user).first()
        if factor is None or factor.confirmed_at is None:
            raise ApiError(
                404,
                "second_factor_not_set",
                _("Dit account heeft geen tweestapsverificatie."),
            )
        factor.delete()
        RecoveryCode.objects.filter(user=user).delete()
        target = _target(user)
        ended = end_sessions(user, current=current)
        if ended:
            record(
                AuditCode.SESSION_ENDED,
                actor=actor,
                target=target,
                values={"count": ended, "reason": "second_factor_cleared"},
            )
        revoked = 0
        if revoke_devices:
            devices = list(
                Device.objects.select_for_update()
                .filter(user=user, revoked_at__isnull=True)
                .order_by("id")
            )
            for device in devices:
                revoke_device(user, device.id, actor=actor)
            revoked = len(devices)
        values: dict[str, AuditValue] = {"via": via, "devices_revoked": revoked}
        record(AuditCode.SECOND_FACTOR_CLEARED, actor=actor, target=target, values=values)
        if workspace_id is not None:
            record(
                AuditCode.SECOND_FACTOR_CLEARED,
                actor=actor,
                target=target,
                workspace_id=workspace_id,
                values=values,
            )
        if via == "admin":
            notify(user, AccountNotice.Code.SECOND_FACTOR_CLEARED, actor_name=actor_name)
        else:
            notify(user, AccountNotice.Code.SECOND_FACTOR_CLEARED_BY_COMMAND, actor_name="")
    return revoked


def purge_unconfirmed(*, now: datetime) -> int:
    """Remove set-ups begun more than an hour ago and never confirmed, and forget a new
    phone that was begun that long ago (the old one keeps working)."""
    with transaction.atomic():
        deleted, _counts = SecondFactor.objects.filter(
            confirmed_at__isnull=True, updated_at__lt=now - UNCONFIRMED_LIFETIME
        ).delete()
        SecondFactor.objects.filter(pending_since__lt=now - UNCONFIRMED_LIFETIME).update(
            pending_secret_sealed=None, pending_since=None
        )
    return deleted


# --- A new phone ----------------------------------------------------------------------


def begin_replacement(
    user: User,
    *,
    host: str,
    code: str,
    recovery_code: str,
    now: datetime,
    client_ip: str | None,
) -> SetupSecret:
    """A new phone ("Nieuwe telefoon", S 7): after a code from the old app (or a recovery
    code, which is used up), a new secret to scan. The old one keeps working until the new
    one is confirmed; a new phone begun earlier is replaced."""
    if recovery_code:
        verify_recovery_code(user, recovery_code, now=now, client_ip=client_ip)
    else:
        verify_code(user, code, now=now, client_ip=client_ip)
    secret = totp.new_secret()
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        factor = (
            SecondFactor.objects.select_for_update()
            .filter(user=user, confirmed_at__isnull=False)
            .first()
        )
        if factor is None:
            raise _not_set()
        factor.pending_secret_sealed = seal_secret(user.id, secret)
        factor.pending_since = now
        factor.save(update_fields=["pending_secret_sealed", "pending_since", "updated_at"])
    return SetupSecret(
        secret_text=totp.grouped_secret(secret),
        otpauth_uri=totp.otpauth_uri(secret, host=host, email=user.email),
    )


def confirm_replacement(
    user: User, code: str, *, now: datetime, client_ip: str | None
) -> ConfirmedSetup:
    """Swap to the new phone with a code from its app, in one step: the old secret stops,
    the counters start again, ten new recovery codes replace the old ones (they may have
    been kept with the old phone). 409 `second_factor_setup_missing` without a new phone
    begun within the hour; 400 `second_factor_invalid`; 409 `second_factor_clock_drift`."""
    with transaction.atomic():
        User.objects.select_for_update(no_key=True).filter(pk=user.pk).first()
        factor = (
            SecondFactor.objects.select_for_update()
            .filter(user=user, confirmed_at__isnull=False)
            .first()
        )
        if (
            factor is None
            or factor.pending_secret_sealed is None
            or factor.pending_since is None
            or now - factor.pending_since > UNCONFIRMED_LIFETIME
        ):
            raise ApiError(
                409,
                "second_factor_setup_missing",
                _("Begin opnieuw met Nieuwe telefoon."),
            )
        secret = open_secret(user.id, factor.pending_secret_sealed)
        step = totp.match(secret, code, now=now, last_used_step=0)
        if step is None:
            if totp.drift_steps(secret, code, now=now) is not None:
                raise _drift_error()
            raise _invalid(400)
        factor.secret_sealed = factor.pending_secret_sealed
        factor.pending_secret_sealed = None
        factor.pending_since = None
        factor.confirmed_at = now
        factor.last_used_step = step
        _reset_counters(factor)
        factor.save()
        codes = _store_new_recovery_codes(user)
        record(
            AuditCode.SECOND_FACTOR_ADDED,
            actor=AuditActor.for_user(user, client_ip=client_ip),
            target=_target(user),
            values={"replaced": True},
        )
    return ConfirmedSetup(recovery_codes=codes, passed=_passed(user, "totp"))


def grace_day(moment: datetime) -> str:
    """A day as a notice names it ("4 december")."""
    return formats.date_format(timezone.localtime(moment, NOTICE_ZONE), "j F")

"""Setting up, checking and clearing a second factor (two-step sign-in, plan Task 4)."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory
from pytest_django import Settings

from apps.accounts import totp
from apps.accounts.models import (
    AccountNotice,
    RecoveryCode,
    SecondFactor,
    SignInFailures,
    User,
    UserSession,
)
from apps.accounts.second_factor import (
    FIRST_LOCK,
    MAX_LOCK,
    begin_replacement,
    begin_setup,
    clear_factor,
    confirm_replacement,
    confirm_setup,
    lock_length,
    open_secret,
    passed_without_factor,
    purge_unconfirmed,
    remove_factor,
    renew_recovery_codes,
    verify_code,
    verify_recovery_code,
)
from apps.audit.models import ServerAuditEvent
from apps.audit.services import AuditActor
from apps.common.crypto import DecryptionError
from apps.common.errors import ApiError
from apps.devices.models import Device
from tests.factories import TEST_PASSWORD, UserFactory
from tests.second_factor_support import Factor, give_factor, secret_of

NOW = datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)
# Not the account's password (`TEST_PASSWORD`).
WRONG_PASSWORD = TEST_PASSWORD + "-fout"


@pytest.fixture
def user() -> User:
    return UserFactory.create(name="Kees")


def at(minutes: float) -> datetime:
    return NOW + timedelta(minutes=minutes)


def api_error(call: object) -> ApiError:
    assert callable(call)
    with pytest.raises(ApiError) as caught:
        call()
    return caught.value


def browser(user: User | None = None) -> HttpRequest:
    request = RequestFactory().post("/api/v1/me/second-factor", REMOTE_ADDR="192.0.2.7")
    SessionMiddleware(lambda r: HttpResponse()).process_request(request)
    request.session.save()
    request.user = user or AnonymousUser()
    return request


def server_rows(code: str) -> list[ServerAuditEvent]:
    return list(ServerAuditEvent.objects.filter(code=code).order_by("created_at"))


# --- Set-up ---------------------------------------------------------------------------


def test_set_up_then_confirm_returns_ten_codes_and_stores_only_hashes(user: User) -> None:
    factor = give_factor(user, now=NOW)

    assert len(factor.recovery_codes) == 10
    stored = list(RecoveryCode.objects.filter(user=user).values_list("code_hash", flat=True))
    assert len(stored) == 10
    for code in factor.recovery_codes:
        assert all(code not in h and code.replace("-", "") not in h for h in stored)
    row = SecondFactor.objects.get(user=user)
    assert row.confirmed_at == NOW
    assert factor.secret not in bytes(row.secret_sealed)
    assert open_secret(user.id, row.secret_sealed) == factor.secret
    (added,) = server_rows("second_factor_added")
    assert added.values == {}
    assert added.target_id == user.id


def test_a_sealed_secret_does_not_open_for_another_user(user: User) -> None:
    give_factor(user, now=NOW)
    other = UserFactory.create()
    row = SecondFactor.objects.get(user=user)

    with pytest.raises(DecryptionError):
        open_secret(other.id, row.secret_sealed)


def test_the_set_up_shows_a_link_and_the_grouped_secret(user: User) -> None:
    setup = begin_setup(user, host="brievie.local")

    secret = secret_of(setup.secret_text)
    assert len(secret) == 20
    assert setup.otpauth_uri == totp.otpauth_uri(secret, host="brievie.local", email=user.email)
    assert totp.grouped_secret(secret) == setup.secret_text
    assert SecondFactor.objects.get(user=user).confirmed_at is None


def test_a_wrong_code_does_not_confirm(user: User) -> None:
    setup = begin_setup(user, host="brievie.local")
    secret = secret_of(setup.secret_text)
    wrong = totp.code_at(secret, totp.step_of(NOW) + 40)

    error = api_error(lambda: confirm_setup(user, wrong, now=NOW, client_ip=None))

    assert (error.status, error.code) == (400, "second_factor_invalid")
    assert SecondFactor.objects.get(user=user).confirmed_at is None
    assert not RecoveryCode.objects.exists()


def test_drift_hint_at_three_steps(user: User) -> None:
    setup = begin_setup(user, host="brievie.local")
    secret = secret_of(setup.secret_text)
    drifted = totp.code_at(secret, totp.step_of(NOW) - 3)

    error = api_error(lambda: confirm_setup(user, drifted, now=NOW, client_ip=None))

    assert (error.status, error.code) == (409, "second_factor_clock_drift")
    assert error.message == (
        "De klok van je telefoon of van de server loopt niet gelijk. Zet op je telefoon de "
        "tijd op automatisch."
    )


def test_a_confirmed_factor_refuses_a_second_set_up(user: User) -> None:
    give_factor(user, now=NOW)

    error = api_error(lambda: begin_setup(user, host="brievie.local"))

    assert (error.status, error.code) == (409, "second_factor_exists")


def test_confirm_without_a_set_up_is_refused(user: User) -> None:
    error = api_error(lambda: confirm_setup(user, "123456", now=NOW, client_ip=None))

    assert error.code == "second_factor_setup_missing"


def test_a_new_set_up_replaces_an_unfinished_one(user: User) -> None:
    first = secret_of(begin_setup(user, host="brievie.local").secret_text)
    second = secret_of(begin_setup(user, host="brievie.local").secret_text)

    assert first != second
    assert open_secret(user.id, SecondFactor.objects.get(user=user).secret_sealed) == second


def test_unconfirmed_set_ups_are_purged_after_an_hour(user: User) -> None:
    begin_setup(user, host="brievie.local")
    confirmed_user = UserFactory.create()
    give_factor(confirmed_user, now=NOW)
    SecondFactor.objects.update(updated_at=NOW - timedelta(minutes=61))

    assert purge_unconfirmed(now=NOW) == 1

    assert list(SecondFactor.objects.values_list("user_id", flat=True)) == [confirmed_user.id]


def test_a_recent_unconfirmed_set_up_stays(user: User) -> None:
    begin_setup(user, host="brievie.local")
    SecondFactor.objects.update(updated_at=NOW - timedelta(minutes=59))

    assert purge_unconfirmed(now=NOW) == 0


# --- Codes ----------------------------------------------------------------------------


def test_a_code_passes_once(user: User) -> None:
    factor = give_factor(user, now=NOW)

    passed = verify_code(user, factor.code(NOW), now=NOW, client_ip=None)

    assert (passed.user_id, passed.method) == (user.id, "totp")
    assert SecondFactor.objects.get(user=user).last_used_step == totp.step_of(NOW)


def test_code_for_a_used_step_refused(user: User) -> None:
    factor = give_factor(user, now=NOW)
    verify_code(user, factor.code(NOW), now=NOW, client_ip=None)

    error = api_error(lambda: verify_code(user, factor.code(NOW), now=NOW, client_ip=None))

    assert (error.status, error.code) == (400, "second_factor_invalid")
    # Not even a code of an earlier step in the window.
    earlier = factor.code(NOW, offset=-1)
    error = api_error(lambda: verify_code(user, earlier, now=NOW, client_ip=None))
    assert error.code == "second_factor_invalid"


def test_a_sign_in_answers_401_for_a_wrong_code(user: User) -> None:
    give_factor(user, now=NOW)
    factor_id = SecondFactor.objects.get(user=user).id

    error = api_error(
        lambda: verify_code(user, "000000", now=NOW, client_ip=None, factor_id=factor_id)
    )

    assert (error.status, error.code) == (401, "second_factor_invalid")
    assert error.message == "Code klopt niet. Probeer het opnieuw."


def test_a_sign_in_that_began_with_another_factor_has_expired(user: User) -> None:
    factor = give_factor(user, now=NOW)
    other_id = UserFactory.create().id  # not the factor's id

    error = api_error(
        lambda: verify_code(user, factor.code(NOW), now=NOW, client_ip=None, factor_id=other_id)
    )

    assert (error.status, error.code) == (401, "sign_in_expired")
    assert SecondFactor.objects.get(user=user).last_used_step != totp.step_of(NOW)


def test_without_a_factor_a_code_is_refused(user: User) -> None:
    error = api_error(lambda: verify_code(user, "123456", now=NOW, client_ip=None))

    assert (error.status, error.code) == (409, "second_factor_not_set")


def test_a_wrong_code_is_counted_and_logged(user: User) -> None:
    give_factor(user, now=NOW)

    api_error(lambda: verify_code(user, "000000", now=NOW, client_ip="192.0.2.7"))

    row = SecondFactor.objects.get(user=user)
    assert (row.failed_count, row.last_failed_at) == (1, NOW)
    (failed,) = server_rows("sign_in_failed")
    assert failed.values == {"step": "second_factor"}
    assert failed.client_ip == "192.0.2.7"


def wrong_codes(user: User, count: int, *, now: datetime) -> ApiError:
    error: ApiError | None = None
    for _ in range(count):
        error = api_error(lambda: verify_code(user, "000000", now=now, client_ip=None))
    assert error is not None
    return error


def test_lockout_doubles_and_caps(user: User) -> None:
    factor = give_factor(user, now=NOW)

    error = wrong_codes(user, 5, now=at(0))
    assert (error.status, error.code) == (429, "second_factor_locked")
    assert error.headers["Retry-After"] == str(15 * 60 + 1)
    row = SecondFactor.objects.get(user=user)
    assert (row.locked_until, row.lockouts) == (at(15), 1)

    # Locked: even the right code answers 429, and is not used up.
    right = factor.code(at(1))
    error = api_error(lambda: verify_code(user, right, now=at(1), client_ip=None))
    assert error.code == "second_factor_locked"

    wrong_codes(user, 5, now=at(16))
    assert SecondFactor.objects.get(user=user).locked_until == at(16 + 30)
    assert lock_length(3) == FIRST_LOCK * 4
    assert lock_length(7) == timedelta(hours=16)
    assert lock_length(8) == MAX_LOCK
    assert lock_length(40) == MAX_LOCK

    later = at(16 + 31)
    verify_code(user, factor.code(later), now=later, client_ip=None)
    row = SecondFactor.objects.get(user=user)
    assert (row.failed_count, row.lockouts, row.locked_until) == (0, 0, None)


def test_a_lockout_tells_the_account_and_the_log(user: User) -> None:
    give_factor(user, now=NOW)

    wrong_codes(user, 5, now=NOW)

    (notice,) = AccountNotice.objects.filter(user=user, code="second_factor_locked")
    assert notice.values == {}
    (locked,) = server_rows("sign_in_locked_out")
    assert locked.values == {"via": "second_factor"}
    assert len(server_rows("sign_in_failed")) == 5


def test_four_wrong_then_one_more_16_minutes_later_locks(user: User) -> None:
    # Security review of PR #52, point 1: no window that starts the count again.
    give_factor(user, now=NOW)

    wrong_codes(user, 4, now=at(0))
    error = wrong_codes(user, 1, now=at(16))

    assert error.code == "second_factor_locked"
    assert SecondFactor.objects.get(user=user).locked_until == at(16 + 15)


def test_slow_guessing_keeps_locking_and_doubling(user: User) -> None:
    give_factor(user, now=NOW)

    for hour in range(4):
        wrong_codes(user, 4, now=at(hour * 60))
    row = SecondFactor.objects.get(user=user)

    assert row.lockouts >= 2
    assert row.locked_until is not None


def test_the_count_starts_again_after_a_day_without_wrong_codes(user: User) -> None:
    give_factor(user, now=NOW)

    wrong_codes(user, 4, now=at(0))
    wrong_codes(user, 1, now=at(24 * 60 + 1))

    row = SecondFactor.objects.get(user=user)
    assert (row.failed_count, row.locked_until) == (1, None)


def test_a_right_code_starts_the_count_again(user: User) -> None:
    factor = give_factor(user, now=NOW)
    wrong_codes(user, 4, now=at(0))

    verify_code(user, factor.code(at(1)), now=at(1), client_ip=None)

    row = SecondFactor.objects.get(user=user)
    assert (row.failed_count, row.last_failed_at) == (0, None)


def test_the_first_wrong_code_at_sign_in_tells_the_account_once(user: User) -> None:
    give_factor(user, now=NOW)
    factor_id = SecondFactor.objects.get(user=user).id
    for minute in range(3):
        moment = at(minute)
        with pytest.raises(ApiError):
            verify_code(user, "000000", now=moment, client_ip=None, factor_id=factor_id)

    (notice,) = AccountNotice.objects.filter(user=user, seen_at=None)
    assert notice.code == "second_factor_locked"


def test_a_wrong_code_in_a_session_tells_nobody(user: User) -> None:
    give_factor(user, now=NOW)

    wrong_codes(user, 1, now=NOW)

    assert not AccountNotice.objects.filter(user=user).exists()


def test_a_long_lock_is_told_in_hours(user: User) -> None:
    give_factor(user, now=NOW)
    SecondFactor.objects.filter(user=user).update(lockouts=5)

    error = wrong_codes(user, 5, now=NOW)

    assert error.message == "Te vaak een verkeerde code. Probeer het over 8 uur opnieuw."


# --- Recovery codes -------------------------------------------------------------------


def test_a_recovery_code_works_once_ignoring_case_and_hyphen(user: User) -> None:
    factor = give_factor(user, now=NOW)
    code = factor.recovery_codes[3]

    passed = verify_recovery_code(user, code.lower().replace("-", ""), now=NOW, client_ip=None)

    assert passed.method == "recovery_code"
    error = api_error(lambda: verify_recovery_code(user, code, now=NOW, client_ip=None))
    assert error.code == "second_factor_invalid"
    (used,) = server_rows("second_factor_recovery_used")
    assert used.values == {"left": 9}
    (notice,) = AccountNotice.objects.filter(user=user, code="second_factor_recovery_used")
    assert notice.values == {"count": "9"}


def test_another_accounts_recovery_code_does_not_work(user: User) -> None:
    give_factor(user, now=NOW)
    other = give_factor(UserFactory.create(), now=NOW)

    error = api_error(
        lambda: verify_recovery_code(user, other.recovery_codes[0], now=NOW, client_ip=None)
    )

    assert error.code == "second_factor_invalid"


def test_wrong_recovery_codes_count_for_the_same_lock(user: User) -> None:
    factor = give_factor(user, now=NOW)
    for _ in range(5):
        api_error(lambda: verify_recovery_code(user, "AAAAA-AAAAA", now=NOW, client_ip=None))

    error = api_error(
        lambda: verify_recovery_code(user, factor.recovery_codes[0], now=NOW, client_ip=None)
    )

    assert error.code == "second_factor_locked"
    assert RecoveryCode.objects.filter(user=user, used_at__isnull=True).count() == 10
    assert {r.values["step"] for r in server_rows("sign_in_failed")} == {"recovery_code"}


def test_renewal_stops_the_old_codes(user: User) -> None:
    factor = give_factor(user, now=NOW)

    new = renew_recovery_codes(user, factor.code(NOW), now=NOW, client_ip=None)

    assert len(new) == 10
    assert set(new).isdisjoint(factor.recovery_codes)
    error = api_error(
        lambda: verify_recovery_code(user, factor.recovery_codes[0], now=NOW, client_ip=None)
    )
    assert error.code == "second_factor_invalid"
    verify_recovery_code(user, new[0], now=NOW, client_ip=None)
    assert len(server_rows("second_factor_recovery_renewed")) == 1


def test_renewal_needs_a_right_code(user: User) -> None:
    factor = give_factor(user, now=NOW)

    error = api_error(lambda: renew_recovery_codes(user, "000000", now=NOW, client_ip=None))

    assert error.code == "second_factor_invalid"
    verify_recovery_code(user, factor.recovery_codes[0], now=NOW, client_ip=None)


# --- Removing -------------------------------------------------------------------------


def test_remove_needs_the_password_and_a_code(user: User) -> None:
    factor = give_factor(user, now=NOW)
    request = browser(user)

    remove_factor(request, user, password=TEST_PASSWORD, code=factor.code(NOW), now=NOW)

    assert not SecondFactor.objects.filter(user=user).exists()
    assert not RecoveryCode.objects.filter(user=user).exists()
    assert len(server_rows("second_factor_removed")) == 1


def test_remove_with_a_wrong_password_counts_like_a_sign_in(user: User) -> None:
    factor = give_factor(user, now=NOW)

    error = api_error(
        lambda: remove_factor(
            browser(user), user, password=WRONG_PASSWORD, code=factor.code(NOW), now=NOW
        )
    )

    assert (error.status, error.code) == (400, "wrong_password")
    assert SignInFailures.objects.get().failures == 1
    assert SecondFactor.objects.filter(user=user).exists()
    for _ in range(4):
        api_error(
            lambda: remove_factor(
                browser(user), user, password=WRONG_PASSWORD, code="000000", now=NOW
            )
        )
    error = api_error(
        lambda: remove_factor(
            browser(user), user, password=TEST_PASSWORD, code=factor.code(NOW), now=NOW
        )
    )
    assert (error.status, error.code) == (429, "too_many_attempts")
    assert server_rows("sign_in_locked_out")[0].values == {"via": "second_factor_remove"}


def test_remove_with_a_wrong_code_is_refused(user: User) -> None:
    give_factor(user, now=NOW)

    error = api_error(
        lambda: remove_factor(browser(user), user, password=TEST_PASSWORD, code="000000", now=NOW)
    )

    assert (error.status, error.code) == (400, "second_factor_invalid")
    assert SecondFactor.objects.filter(user=user).exists()


def test_remove_while_required_is_refused(settings: Settings, user: User) -> None:
    settings.BRIEVIE_REQUIRE_SECOND_FACTOR = "everyone"
    factor = give_factor(user, now=NOW)

    error = api_error(
        lambda: remove_factor(
            browser(user), user, password=TEST_PASSWORD, code=factor.code(NOW), now=NOW
        )
    )

    assert (error.status, error.code) == (409, "second_factor_required")
    assert SecondFactor.objects.filter(user=user).exists()


def test_remove_ends_the_other_sessions(user: User) -> None:
    factor = give_factor(user, now=NOW)
    request = browser(user)
    other = UserSession.objects.create(
        user=user, session_key="b" * 32, method="password", last_used_at=NOW
    )
    key = request.session.session_key
    assert key is not None
    UserSession.objects.create(user=user, session_key=key, method="password", last_used_at=NOW)

    remove_factor(request, user, password=TEST_PASSWORD, code=factor.code(NOW), now=NOW)

    assert not UserSession.objects.filter(pk=other.pk).exists()
    assert UserSession.objects.filter(session_key=request.session.session_key).exists()
    (ended,) = server_rows("session_ended")
    assert ended.values == {"count": 1, "reason": "second_factor_removed"}


def test_remove_leaves_the_own_session_without_the_second_step(user: User) -> None:
    # The step it passed was with the factor that is now gone: on the public route the
    # session no longer counts.
    factor = give_factor(user, now=NOW)
    request = browser(user)
    key = request.session.session_key
    assert key is not None
    own = UserSession.objects.create(
        user=user, session_key=key, method="password", last_used_at=NOW, second_factor=True
    )

    remove_factor(request, user, password=TEST_PASSWORD, code=factor.code(NOW), now=NOW)

    own.refresh_from_db()
    assert own.second_factor is False


# --- Clearing -------------------------------------------------------------------------


def devices_of(user: User) -> list[Device]:
    return [
        Device.objects.create(user=user, name="iPhone", token_hash="d" * 63 + "1"),
        Device.objects.create(user=user, name="iPad", token_hash="d" * 63 + "2"),
    ]


def test_clear_ends_sessions_unpairs_devices_and_tells(user: User) -> None:
    give_factor(user, now=NOW)
    devices_of(user)
    UserSession.objects.create(user=user, session_key="c" * 32, method="password", last_used_at=NOW)
    admin = UserFactory.create(name="Anne", is_server_admin=True)

    revoked = clear_factor(
        user,
        revoke_devices=True,
        via="admin",
        actor=AuditActor.for_user(admin),
        actor_name=admin.name,
    )

    assert revoked == 2
    assert not SecondFactor.objects.filter(user=user).exists()
    assert not RecoveryCode.objects.filter(user=user).exists()
    assert not UserSession.objects.filter(user=user).exists()
    assert not Device.objects.filter(user=user, revoked_at__isnull=True).exists()
    (cleared,) = server_rows("second_factor_cleared")
    assert cleared.values == {"via": "admin", "devices_revoked": 2}
    assert cleared.actor_id == admin.id
    (ended,) = server_rows("session_ended")
    assert ended.values == {"count": 1, "reason": "second_factor_cleared"}
    (notice,) = AccountNotice.objects.filter(user=user, code="second_factor_cleared")
    assert notice.actor_name == "Anne"


def test_clear_keeps_devices_when_asked(user: User) -> None:
    give_factor(user, now=NOW)
    devices_of(user)

    revoked = clear_factor(
        user, revoke_devices=False, via="admin", actor=AuditActor.system(), actor_name="Anne"
    )

    assert revoked == 0
    assert Device.objects.filter(user=user, revoked_at__isnull=True).count() == 2
    assert server_rows("second_factor_cleared")[0].values == {"via": "admin", "devices_revoked": 0}


def test_clear_by_command_has_its_own_notice(user: User) -> None:
    give_factor(user, now=NOW)

    clear_factor(user, revoke_devices=True, via="command", actor=AuditActor.system(), actor_name="")

    (notice,) = AccountNotice.objects.filter(user=user)
    assert notice.code == "second_factor_cleared_by_command"
    assert server_rows("second_factor_cleared")[0].actor_kind == "system"


def test_clear_without_a_factor_is_404(user: User) -> None:
    error = api_error(
        lambda: clear_factor(
            user, revoke_devices=True, via="admin", actor=AuditActor.system(), actor_name="Anne"
        )
    )

    assert (error.status, error.code) == (404, "second_factor_not_set")


# --- What never leaks -----------------------------------------------------------------


def test_no_audit_value_notice_or_log_holds_a_secret_or_code(
    user: User, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    factor: Factor = give_factor(user, now=NOW)
    verify_code(user, factor.code(NOW), now=NOW, client_ip=None)
    api_error(lambda: verify_code(user, factor.code(NOW), now=NOW, client_ip=None))
    verify_recovery_code(user, factor.recovery_codes[0], now=NOW, client_ip=None)
    wrong_codes(user, 5, now=NOW)
    renewed = renew_recovery_codes(user, factor.code(at(40)), now=at(40), client_ip=None)
    clear_factor(user, revoke_devices=True, via="admin", actor=AuditActor.system(), actor_name="A")

    secrets = {
        totp.grouped_secret(factor.secret).replace(" ", ""),
        factor.secret.hex(),
        factor.code(NOW),
        factor.code(at(40)),
        *factor.recovery_codes,
        *(code.replace("-", "") for code in factor.recovery_codes),
        *renewed,
    }
    stored = [str(r.values) for r in ServerAuditEvent.objects.all()]
    stored += [f"{n.values} {n.actor_name}" for n in AccountNotice.objects.all()]
    stored += [record.getMessage() for record in caplog.records]
    for text in stored:
        assert not any(secret in text for secret in secrets), text


def test_a_pass_cannot_be_made_outside_the_module(user: User) -> None:
    from apps.accounts.second_factor import SecondFactorPassed

    with pytest.raises(TypeError):
        SecondFactorPassed(user_id=user.id, method="totp", issuer=object())
    assert passed_without_factor(user).method == "none"


# --- A new phone (plan Task 16) ---------------------------------------------------------


def new_phone(user: User, factor: Factor, *, at_time: datetime = NOW) -> bytes:
    setup = begin_replacement(
        user,
        host="brievie.local",
        code=factor.code(at_time),
        recovery_code="",
        now=at_time,
        client_ip=None,
    )
    return secret_of(setup.secret_text)


def test_a_new_phone_needs_a_code_of_the_old_one(user: User) -> None:
    give_factor(user, now=NOW)

    error = api_error(
        lambda: begin_replacement(
            user, host="h", code="000000", recovery_code="", now=NOW, client_ip=None
        )
    )

    assert (error.status, error.code) == (400, "second_factor_invalid")
    assert SecondFactor.objects.get(user=user).pending_secret_sealed is None


def test_a_recovery_code_may_start_a_new_phone(user: User) -> None:
    factor = give_factor(user, now=NOW)

    begin_replacement(
        user,
        host="h",
        code="",
        recovery_code=factor.recovery_codes[0],
        now=NOW,
        client_ip=None,
    )

    assert SecondFactor.objects.get(user=user).pending_since == NOW
    assert RecoveryCode.objects.filter(user=user, used_at__isnull=True).count() == 9


def test_the_old_phone_works_until_the_new_one_is_confirmed(user: User) -> None:
    factor = give_factor(user, now=NOW)
    new_secret = new_phone(user, factor)

    later = NOW + timedelta(minutes=1)
    verify_code(user, factor.code(later), now=later, client_ip=None)
    new_code = totp.code_at(new_secret, totp.step_of(later))
    error = api_error(lambda: verify_code(user, new_code, now=later, client_ip=None))
    assert error.code == "second_factor_invalid"


def test_confirming_the_new_phone_swaps_in_one_step(user: User) -> None:
    factor = give_factor(user, now=NOW)
    new_secret = new_phone(user, factor)
    later = NOW + timedelta(minutes=2)

    confirmed = confirm_replacement(
        user, totp.code_at(new_secret, totp.step_of(later)), now=later, client_ip=None
    )

    row = SecondFactor.objects.get(user=user)
    assert open_secret(user.id, row.secret_sealed) == new_secret
    assert (row.pending_secret_sealed, row.pending_since) == (None, None)
    assert len(confirmed.recovery_codes) == 10
    assert set(confirmed.recovery_codes).isdisjoint(factor.recovery_codes)
    (replaced,) = [r for r in server_rows("second_factor_added") if r.values]
    assert replaced.values == {"replaced": True}
    old = factor.code(later + timedelta(minutes=1))
    error = api_error(
        lambda: verify_code(user, old, now=later + timedelta(minutes=1), client_ip=None)
    )
    assert error.code == "second_factor_invalid"


def test_confirming_without_a_new_phone_is_refused(user: User) -> None:
    give_factor(user, now=NOW)

    error = api_error(lambda: confirm_replacement(user, "123456", now=NOW, client_ip=None))

    assert (error.status, error.code) == (409, "second_factor_setup_missing")


def test_a_wrong_code_of_the_new_phone_keeps_the_old_one(user: User) -> None:
    factor = give_factor(user, now=NOW)
    new_phone(user, factor)

    error = api_error(lambda: confirm_replacement(user, "000000", now=NOW, client_ip=None))

    assert error.code == "second_factor_invalid"
    row = SecondFactor.objects.get(user=user)
    assert open_secret(user.id, row.secret_sealed) == factor.secret
    assert row.pending_secret_sealed is not None


def test_a_new_phone_left_for_an_hour_is_forgotten(user: User) -> None:
    factor = give_factor(user, now=NOW)
    new_secret = new_phone(user, factor)
    later = NOW + timedelta(minutes=61)

    purge_unconfirmed(now=later)

    row = SecondFactor.objects.get(user=user)
    assert (row.pending_secret_sealed, row.pending_since) == (None, None)
    error = api_error(
        lambda: confirm_replacement(
            user, totp.code_at(new_secret, totp.step_of(later)), now=later, client_ip=None
        )
    )
    assert error.code == "second_factor_setup_missing"


# --- Wrong codes when confirming (security review of PR #52, point 7) --------------------


def test_five_wrong_codes_end_a_set_up(user: User) -> None:
    begin_setup(user, host="brievie.local")
    for _ in range(4):
        error = api_error(lambda: confirm_setup(user, "000000", now=NOW, client_ip=None))
        assert error.code == "second_factor_invalid"
    assert SecondFactor.objects.get(user=user).failed_count == 4

    error = api_error(lambda: confirm_setup(user, "000000", now=NOW, client_ip=None))

    assert (error.status, error.code) == (409, "second_factor_setup_missing")
    assert not SecondFactor.objects.filter(user=user).exists()


def test_wrong_codes_of_a_new_phone_count_for_the_account(user: User) -> None:
    factor = give_factor(user, now=NOW)
    new_phone(user, factor)
    for _ in range(4):
        api_error(lambda: confirm_replacement(user, "000000", now=NOW, client_ip=None))

    error = api_error(lambda: confirm_replacement(user, "000000", now=NOW, client_ip=None))

    assert error.code == "second_factor_locked"
    row = SecondFactor.objects.get(user=user)
    assert open_secret(user.id, row.secret_sealed) == factor.secret

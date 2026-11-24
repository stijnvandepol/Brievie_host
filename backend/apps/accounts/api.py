from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.utils.translation import gettext as _
from ninja import Router, Status

from apps.accounts import (
    notices,
    resets,
    second_factor_account,
    services,
    signin,
    signin_limits,
    user_actions,
)
from apps.accounts.auth import session_auth
from apps.accounts.models import User
from apps.accounts.notices import NoticeView
from apps.accounts.pending_auth import pending_setup_auth
from apps.accounts.resets import IssuedReset
from apps.accounts.schemas import (
    ClearSecondFactorIn,
    ClearSecondFactorOut,
    LanguageIn,
    LoginIn,
    NoticeOut,
    NoticesSeenIn,
    PasswordChangeIn,
    PasswordResetIn,
    PasswordResetOut,
    ReauthenticateIn,
    RecoveryCodesOut,
    RemoveSecondFactorIn,
    ReplaceSecondFactorIn,
    SecondFactorCodeIn,
    SecondFactorOut,
    SessionOut,
    SetupIn,
    SetupOut,
    SetupSecretOut,
    SetupStatusOut,
    SignInCodeIn,
    SignInStepOut,
    UserOut,
)
from apps.accounts.second_factor import SetupSecret
from apps.accounts.second_factor_account import SecondFactorStatus
from apps.accounts.services import SessionView
from apps.accounts.signin import SignInResult, StepNeeded
from apps.common.errors import ApiError
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import CallerThrottle, ClientAddressThrottle
from apps.devices import services as device_services
from apps.devices.models import Device
from apps.devices.schemas import DeviceOut
from apps.workspaces.schemas import WorkspaceOut

router = Router(tags=["accounts"])

SECOND_STEP = (
    " 202 when a second step follows (SignInStepOut): the browser is not signed in yet;"
    " `code` asks verifySignInCode, `setup` asks to set up two-step verification first."
)


def step_out(result: StepNeeded) -> Status[SignInStepOut]:
    return Status(
        202, SignInStepOut(step=result.step, expires_in_seconds=result.expires_in_seconds)
    )


def signed_in_or_step(result: SignInResult) -> Status[User] | Status[SignInStepOut]:
    if isinstance(result, StepNeeded):
        return step_out(result)
    return Status(200, result.user)


@router.get(
    "/setup",
    response=SetupStatusOut,
    auth=None,
    operation_id="getSetupStatus",
)
def get_setup_status(request: HttpRequest) -> SetupStatusOut:
    return SetupStatusOut(needs_setup=services.needs_setup())


@router.post(
    "/setup",
    response={
        201: SetupOut,
        202: SignInStepOut,
        403: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **BODY_ERRORS,
    },
    auth=None,
    throttle=ClientAddressThrottle("setup", "10/10m"),
    description="Create the first account and workspace, and sign in." + SECOND_STEP,
    operation_id="setupServer",
)
def setup_server(
    request: HttpRequest, payload: SetupIn
) -> Status[SetupOut] | Status[SignInStepOut]:
    done = services.setup_server(request, **payload.model_dump())
    if isinstance(done.result, StepNeeded):
        return step_out(done.result)
    membership = done.membership
    user = UserOut.model_validate(membership.user, from_attributes=True)
    return Status(201, SetupOut(user=user, workspace=WorkspaceOut.from_membership(membership)))


@router.post(
    "/auth/login",
    response={200: UserOut, 202: SignInStepOut, 401: ErrorOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    # Counts failed sign-ins only (see services.LOGIN_FAILURES).
    throttle=services.LOGIN_FAILURES,
    description=(
        "429 too_many_attempts with Retry-After: five wrong passwords for this email from "
        "this address, thirty from this address, or, from a browser that never signed in "
        "to this account, more than ten for the account from anywhere (then a wait that "
        "doubles from 15 seconds to at most 15 minutes)." + SECOND_STEP
    ),
    operation_id="login",
)
def login(
    request: HttpRequest, response: HttpResponse, payload: LoginIn
) -> Status[User] | Status[SignInStepOut]:
    result = services.login(request, email=payload.email, password=payload.password)
    if isinstance(result, StepNeeded):
        # Not signed in yet: the browser becomes known only when the sign-in completes.
        return step_out(result)
    # This browser is known for the account from now on: never slowed by somebody
    # else's wrong passwords (signin_limits).
    signin_limits.remember_browser(request, response, payload.email)
    return Status(200, result.user)


@router.post(
    "/auth/login/code",
    response={200: UserOut, 401: ErrorOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    throttle=ClientAddressThrottle("second_factor_code", "10/m"),
    description=(
        "The second step of a sign-in that answered 202 with step `code`: the six digits "
        "from the authenticator app (`code`) or a recovery code (`recovery_code`), exactly "
        "one (422 otherwise). 200 signs the browser in. 401 second_factor_invalid for a "
        "wrong, used or old code; 401 sign_in_expired without a pending sign-in (it lives "
        "five minutes, and five wrong codes end it: sign in with the password again); 429 "
        "second_factor_locked (Retry-After) after five wrong codes for the account, from "
        "anywhere, within 15 minutes: the lock doubles each time, up to a day."
    ),
    operation_id="verifySignInCode",
)
def verify_sign_in_code(
    request: HttpRequest, response: HttpResponse, payload: SignInCodeIn
) -> User:
    if bool(payload.code.strip()) == bool(payload.recovery_code.strip()):
        raise ApiError(422, "validation_error", _("Vul de code uit de app in, of een herstelcode."))
    user = signin.verify_sign_in_code(
        request, code=payload.code, recovery_code=payload.recovery_code
    )
    # The sign-in is complete: this browser is known for the account from now on.
    signin_limits.remember_browser(request, response, user.email)
    return user


@router.post(
    "/auth/logout",
    response={204: None, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    description="Web session only: a bearer token answers 401 not_authenticated.",
    operation_id="logout",
)
def logout(request: HttpRequest) -> Status[None]:
    services.logout(request)
    return Status(204, None)


@router.get("/me", response={200: UserOut, **AUTH_ERRORS}, operation_id="getMe")
def get_me(request: HttpRequest) -> User:
    return services.current_user(request)


@router.put(
    "/me/language",
    response={200: UserOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    description=(
        "Choose the language of the interface and of the server's messages for the "
        "caller's account (`nl`, `en`, or `` to follow Accept-Language again). The "
        "choice wins over Accept-Language on every later request."
    ),
    operation_id="updateMyLanguage",
)
def update_my_language(request: HttpRequest, payload: LanguageIn) -> User:
    return services.set_language(services.current_user(request), payload.language)


WEB_SESSION_ONLY = "Web session only: a bearer token answers 401 not_authenticated."


@router.get(
    "/me/sessions",
    response={200: list[SessionOut], **AUTH_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY,
    operation_id="listMySessions",
)
def list_my_sessions(request: HttpRequest) -> list[SessionView]:
    return services.list_my_sessions(request)


@router.delete(
    "/me/sessions/{session_id}",
    response={204: None, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY + " Ending the current session signs this browser out.",
    operation_id="endMySession",
)
def end_my_session(request: HttpRequest, session_id: UUID) -> Status[None]:
    services.end_my_session(request, session_id=session_id)
    return Status(204, None)


@router.post(
    "/me/password",
    response={204: None, 429: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    auth=session_auth,
    throttle=CallerThrottle("password", "10/10m"),
    description=WEB_SESSION_ONLY
    + " 400 wrong_password when the current password is wrong; every other session ends.",
    operation_id="changePassword",
)
def change_password(request: HttpRequest, payload: PasswordChangeIn) -> Status[None]:
    services.change_password(
        request, current_password=payload.current_password, new_password=payload.new_password
    )
    return Status(204, None)


@router.post(
    "/auth/reauthenticate",
    response={204: None, 400: ErrorOut, 429: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    auth=session_auth,
    throttle=CallerThrottle("reauthenticate", "10/10m"),
    description=WEB_SESSION_ONLY
    + " Type the password again before an action that needs a recent sign-in (creating a"
    " pairing code answers 403 reauthentication_required otherwise). 400 wrong_password;"
    " wrong passwords count for the same lockout as signing in (429). With two-step"
    " verification on, `code` from the app too: 400 second_factor_invalid, 429"
    " second_factor_locked; the session has then passed the second step.",
    operation_id="reauthenticate",
)
def reauthenticate(request: HttpRequest, payload: ReauthenticateIn) -> Status[None]:
    services.reauthenticate(request, password=payload.password, code=payload.code)
    return Status(204, None)


@router.get(
    "/me/notices",
    response={200: list[NoticeOut], **AUTH_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY + " Notices not dismissed yet, oldest first.",
    operation_id="listMyNotices",
)
def list_my_notices(request: HttpRequest) -> list[NoticeView]:
    return notices.unseen_notices(request)


@router.post(
    "/me/notices/seen",
    response={204: None, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + " Ids of other users' notices, unknown ids and notices this session may not dismiss"
    " are left as they are.",
    operation_id="markNoticesSeen",
)
def mark_notices_seen(request: HttpRequest, payload: NoticesSeenIn) -> Status[None]:
    notices.mark_seen(request, payload.notice_ids)
    return Status(204, None)


@router.post(
    "/users/{user_id}/password-reset",
    response={
        201: PasswordResetOut,
        404: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
    },
    auth=session_auth,
    # Every link writes a row, a notice and audit rows: 30 per caller per 10 minutes.
    throttle=CallerThrottle("password_reset_create", "30/10m", per_path=False),
    description=WEB_SESSION_ONLY
    + " A one-time link (24 hours) with which the member chooses a new password;"
    " the token is in its fragment and is shown only here. An earlier unused link stops"
    " working. A server admin may do this for anyone; a workspace admin only for a member"
    " whose only workspace is theirs and who is no server admin (403 server_admin_only)."
    " 403 own_account for the caller's own account; 404 user_not_found for an unknown"
    " account or one outside the caller's workspaces; 409 account_inactive; 429 after 30"
    " links in 10 minutes by the same caller.",
    operation_id="createPasswordReset",
)
def create_password_reset(
    request: HttpRequest, response: HttpResponse, user_id: UUID
) -> Status[IssuedReset]:
    issued = resets.create_password_reset(request, target_id=user_id)
    response["Cache-Control"] = "no-store"
    return Status(201, issued)


@router.post(
    "/auth/password-reset",
    response={200: UserOut, 202: SignInStepOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    throttle=ClientAddressThrottle("password_reset", "10/10m"),
    description="Set a new password with the token from a reset link's fragment and sign"
    " in; every other session of the account ends. 400 reset_link_invalid for an unknown,"
    " used, expired or revoked link, or when its maker may no longer act on the account;"
    " 422 when the password rules refuse it (the link stays usable). Every paired device"
    " and every other open reset link of the account is revoked." + SECOND_STEP,
    operation_id="completePasswordReset",
)
def complete_password_reset(
    request: HttpRequest, response: HttpResponse, payload: PasswordResetIn
) -> Status[User] | Status[SignInStepOut]:
    result = resets.complete_password_reset(request, token=payload.token, password=payload.password)
    response["Cache-Control"] = "no-store"
    return signed_in_or_step(result)


ADMIN_ACTION = (
    " A server admin may do this for anyone else; a workspace admin only for a member whose"
    " only workspace is theirs and who is no server admin (403 server_admin_only). 403"
    " own_account for the caller's own account; 404 user_not_found for an unknown account or"
    " one outside the caller's workspaces."
)


@router.delete(
    "/users/{user_id}/sessions",
    response={204: None, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + " Ends every web session of the account; the member gets a notice."
    + ADMIN_ACTION,
    operation_id="endUserSessions",
)
def end_user_sessions(request: HttpRequest, user_id: UUID) -> Status[None]:
    user_actions.end_user_sessions(request, target_id=user_id)
    return Status(204, None)


@router.get(
    "/users/{user_id}/devices",
    response={200: list[DeviceOut], 404: ErrorOut, **AUTH_ERRORS, 403: ErrorOut},
    auth=session_auth,
    description=WEB_SESSION_ONLY + " The account's active devices." + ADMIN_ACTION,
    operation_id="listUserDevices",
)
def list_user_devices(request: HttpRequest, user_id: UUID) -> list[Device]:
    return device_services.list_user_devices(request, target_id=user_id)


@router.delete(
    "/users/{user_id}/devices/{device_id}",
    response={204: None, 404: ErrorOut, **SESSION_WRITE_ERRORS},
    auth=session_auth,
    description=WEB_SESSION_ONLY
    + " Revokes the device; the member gets a notice. 404 device_not_found when the"
    " account has no such active device." + ADMIN_ACTION,
    operation_id="revokeUserDevice",
)
def revoke_user_device(request: HttpRequest, user_id: UUID, device_id: UUID) -> Status[None]:
    device_services.revoke_user_device(request, target_id=user_id, device_id=device_id)
    return Status(204, None)


SET_UP = (
    " A web session, or a sign-in that answered 202 with step `setup` (it reaches only the"
    " set-up operations)."
)
SHOWN_ONCE = " The answer is shown once and not stored by the browser (Cache-Control: no-store)."


@router.get(
    "/me/second-factor",
    response={200: SecondFactorOut, **AUTH_ERRORS},
    auth=[session_auth, pending_setup_auth],
    description="Whether the caller has two-step verification, whether a rule asks it, and"
    " how many recovery codes are left." + SET_UP,
    operation_id="getMySecondFactor",
)
def get_my_second_factor(request: HttpRequest) -> SecondFactorStatus:
    return second_factor_account.my_status(request)


@router.post(
    "/me/second-factor/setup",
    response={200: SetupSecretOut, 403: ErrorOut, 409: ErrorOut, 429: ErrorOut, **AUTH_ERRORS},
    auth=[session_auth, pending_setup_auth],
    throttle=ClientAddressThrottle("second_factor_setup", "20/10m"),
    description="Begin setting up two-step verification: a new secret to scan (an"
    " unfinished set-up is replaced). 409 second_factor_exists when it is on; 403"
    " reauthentication_required from a session that signed in more than 15 minutes ago"
    " (call reauthenticate first)." + SET_UP + SHOWN_ONCE,
    operation_id="beginSecondFactorSetup",
)
def begin_second_factor_setup(request: HttpRequest, response: HttpResponse) -> SetupSecretOut:
    setup: SetupSecret = second_factor_account.begin_my_setup(request)
    response["Cache-Control"] = "no-store"
    return SetupSecretOut(secret=setup.secret_text, otpauth_uri=setup.otpauth_uri)


@router.post(
    "/me/second-factor/setup/confirm",
    response={
        200: RecoveryCodesOut,
        400: ErrorOut,
        401: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **BODY_ERRORS,
    },
    auth=[session_auth, pending_setup_auth],
    throttle=ClientAddressThrottle("second_factor_setup", "20/10m"),
    description="Switch two-step verification on with a code from the app; answers ten"
    " recovery codes. A sign-in at step `setup` is then signed in; every other session of"
    " the account ends. 400 second_factor_invalid for a wrong code; 409"
    " second_factor_clock_drift when the phone's or the server's clock is off; 409"
    " second_factor_setup_missing without a set-up begun." + SET_UP + SHOWN_ONCE,
    operation_id="confirmSecondFactorSetup",
)
def confirm_second_factor_setup(
    request: HttpRequest, response: HttpResponse, payload: SecondFactorCodeIn
) -> RecoveryCodesOut:
    codes = second_factor_account.confirm_my_setup(request, code=payload.code)
    response["Cache-Control"] = "no-store"
    return RecoveryCodesOut(recovery_codes=codes)


@router.post(
    "/me/second-factor/recovery-codes",
    response={
        200: RecoveryCodesOut,
        400: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    throttle=CallerThrottle("second_factor", "10/10m"),
    description=WEB_SESSION_ONLY
    + " Ten new recovery codes after a code from the app; the old ones stop working. 400"
    " second_factor_invalid; 429 second_factor_locked." + SHOWN_ONCE,
    operation_id="renewRecoveryCodes",
)
def renew_recovery_codes(
    request: HttpRequest, response: HttpResponse, payload: SecondFactorCodeIn
) -> RecoveryCodesOut:
    codes = second_factor_account.renew_my_recovery_codes(request, code=payload.code)
    response["Cache-Control"] = "no-store"
    return RecoveryCodesOut(recovery_codes=codes)


@router.delete(
    "/me/second-factor",
    response={
        204: None,
        400: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    throttle=CallerThrottle("second_factor", "10/10m"),
    description=WEB_SESSION_ONLY
    + " Switch two-step verification off with the password and a code; every other"
    " session ends. 400 wrong_password (counted like a sign-in), 400"
    " second_factor_invalid; 409 second_factor_required while a rule asks it.",
    operation_id="removeMySecondFactor",
)
def remove_my_second_factor(request: HttpRequest, payload: RemoveSecondFactorIn) -> Status[None]:
    second_factor_account.remove_my_factor(request, password=payload.password, code=payload.code)
    return Status(204, None)


@router.post(
    "/users/{user_id}/second-factor/clear",
    response={
        200: ClearSecondFactorOut,
        400: ErrorOut,
        404: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    throttle=CallerThrottle("second_factor_clear", "10/10m", per_path=False),
    description=WEB_SESSION_ONLY
    + " Clear the two-step verification of somebody who lost their phone: every session of"
    " the account ends, its paired devices are unpaired unless revoke_devices is false, the"
    " member gets a notice. The caller's own password (400 wrong_password, counted like a"
    " sign-in); 403 second_factor_session_required when the caller has two-step"
    " verification but signed in without it; 404 second_factor_not_set when the account"
    " has none." + ADMIN_ACTION,
    operation_id="clearUserSecondFactor",
)
def clear_user_second_factor(
    request: HttpRequest, user_id: UUID, payload: ClearSecondFactorIn
) -> ClearSecondFactorOut:
    revoked = user_actions.clear_user_second_factor(
        request,
        target_id=user_id,
        password=payload.password,
        revoke_devices=payload.revoke_devices,
    )
    return ClearSecondFactorOut(devices_revoked=revoked)


@router.post(
    "/me/second-factor/replacement",
    response={
        200: SetupSecretOut,
        400: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    throttle=CallerThrottle("second_factor", "10/10m"),
    description=WEB_SESSION_ONLY
    + " A new phone: a code from the old app (`code`) or a recovery code (`recovery_code`,"
    " used up), exactly one; answers a new secret to scan. The old app keeps working until"
    " the new one is confirmed. 400 second_factor_invalid; 409 second_factor_not_set; 429"
    " second_factor_locked." + SHOWN_ONCE,
    operation_id="beginSecondFactorReplacement",
)
def begin_second_factor_replacement(
    request: HttpRequest, response: HttpResponse, payload: ReplaceSecondFactorIn
) -> SetupSecretOut:
    if bool(payload.code.strip()) == bool(payload.recovery_code.strip()):
        raise ApiError(422, "validation_error", _("Vul de code uit de app in, of een herstelcode."))
    setup = second_factor_account.begin_my_replacement(
        request, code=payload.code, recovery_code=payload.recovery_code
    )
    response["Cache-Control"] = "no-store"
    return SetupSecretOut(secret=setup.secret_text, otpauth_uri=setup.otpauth_uri)


@router.post(
    "/me/second-factor/replacement/confirm",
    response={
        200: RecoveryCodesOut,
        400: ErrorOut,
        409: ErrorOut,
        429: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    auth=session_auth,
    throttle=CallerThrottle("second_factor", "10/10m"),
    description=WEB_SESSION_ONLY
    + " Move two-step verification to the new phone with a code from its app: the old app"
    " stops, ten new recovery codes replace the old ones, every other session ends. 400"
    " second_factor_invalid; 409 second_factor_clock_drift; 409"
    " second_factor_setup_missing without a new phone begun within the hour." + SHOWN_ONCE,
    operation_id="confirmSecondFactorReplacement",
)
def confirm_second_factor_replacement(
    request: HttpRequest, response: HttpResponse, payload: SecondFactorCodeIn
) -> RecoveryCodesOut:
    codes = second_factor_account.confirm_my_replacement(request, code=payload.code)
    response["Cache-Control"] = "no-store"
    return RecoveryCodesOut(recovery_codes=codes)

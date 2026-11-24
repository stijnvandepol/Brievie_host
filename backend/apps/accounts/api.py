from uuid import UUID

from django.http import HttpRequest, HttpResponse
from ninja import Router, Status

from apps.accounts import notices, resets, services, signin_limits, user_actions
from apps.accounts.auth import session_auth
from apps.accounts.models import User
from apps.accounts.notices import NoticeView
from apps.accounts.resets import IssuedReset
from apps.accounts.schemas import (
    LanguageIn,
    LoginIn,
    NoticeOut,
    NoticesSeenIn,
    PasswordChangeIn,
    PasswordResetIn,
    PasswordResetOut,
    ReauthenticateIn,
    SessionOut,
    SetupIn,
    SetupOut,
    SetupStatusOut,
    SignInStepOut,
    UserOut,
)
from apps.accounts.services import SessionView
from apps.accounts.signin import SignInResult, StepNeeded
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
    " wrong passwords count for the same lockout as signing in (429).",
    operation_id="reauthenticate",
)
def reauthenticate(request: HttpRequest, payload: ReauthenticateIn) -> Status[None]:
    services.reauthenticate(request, password=payload.password)
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

from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from apps.accounts import notices, resets, services
from apps.accounts.auth import session_auth
from apps.accounts.models import User
from apps.accounts.notices import NoticeView
from apps.accounts.resets import IssuedReset
from apps.accounts.schemas import (
    LoginIn,
    NoticeOut,
    NoticesSeenIn,
    PasswordChangeIn,
    PasswordResetIn,
    PasswordResetOut,
    SessionOut,
    SetupIn,
    SetupOut,
    SetupStatusOut,
    UserOut,
)
from apps.accounts.services import SessionView
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import CallerThrottle, ClientAddressThrottle
from apps.workspaces.schemas import WorkspaceOut

router = Router(tags=["accounts"])


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
    response={201: SetupOut, 403: ErrorOut, 409: ErrorOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    throttle=ClientAddressThrottle("setup", "10/10m"),
    operation_id="setupServer",
)
def setup_server(request: HttpRequest, payload: SetupIn) -> Status[SetupOut]:
    membership = services.setup_server(request, **payload.model_dump())
    user = UserOut.model_validate(membership.user, from_attributes=True)
    return Status(201, SetupOut(user=user, workspace=WorkspaceOut.from_membership(membership)))


@router.post(
    "/auth/login",
    response={200: UserOut, 401: ErrorOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    # Counts failed sign-ins only (see services.LOGIN_FAILURES).
    throttle=services.LOGIN_FAILURES,
    operation_id="login",
)
def login(request: HttpRequest, payload: LoginIn) -> User:
    return services.login(request, email=payload.email, password=payload.password)


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
    response={201: PasswordResetOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    description="A one-time link (24 hours) with which the member chooses a new password;"
    " the token is in its fragment and is shown only here. An earlier unused link stops"
    " working. A server admin may do this for anyone; a workspace admin only for a member"
    " whose only workspace is theirs and who is no server admin (403 server_admin_only)."
    " 403 own_account for the caller's own account; 404 user_not_found for an unknown"
    " account or one outside the caller's workspaces; 409 account_inactive.",
    operation_id="createPasswordReset",
)
def create_password_reset(request: HttpRequest, user_id: UUID) -> Status[IssuedReset]:
    return Status(201, resets.create_password_reset(request, target_id=user_id))


@router.post(
    "/auth/password-reset",
    response={200: UserOut, 429: ErrorOut, **BODY_ERRORS},
    auth=None,
    throttle=ClientAddressThrottle("password_reset", "10/10m"),
    description="Set a new password with the token from a reset link's fragment and sign"
    " in; every other session of the account ends. 400 reset_link_invalid for an unknown,"
    " used, expired or revoked link, or when its maker may no longer act on the account;"
    " 422 when the password rules refuse it (the link stays usable).",
    operation_id="completePasswordReset",
)
def complete_password_reset(request: HttpRequest, payload: PasswordResetIn) -> User:
    return resets.complete_password_reset(request, token=payload.token, password=payload.password)

from django.http import HttpRequest
from ninja import Router, Status

from apps.accounts import services
from apps.accounts.auth import session_auth
from apps.accounts.models import User
from apps.accounts.schemas import LoginIn, SetupIn, SetupOut, SetupStatusOut, UserOut
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.common.throttling import ClientAddressThrottle
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
    # Every failure writes an audit row: one address must not grow the table without
    # bound. axes still locks per email and address.
    throttle=ClientAddressThrottle("login", "30/10m"),
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

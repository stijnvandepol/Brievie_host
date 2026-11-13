from django.http import HttpRequest
from ninja import Router, Status
from ninja.security import django_auth

from apps.accounts import services
from apps.accounts.models import User
from apps.accounts.schemas import LoginIn, SetupIn, SetupOut, SetupStatusOut, UserOut
from apps.common.schemas import ErrorOut
from apps.workspaces.schemas import WorkspaceOut

router = Router(tags=["accounts"])


@router.get(
    "/setup",
    # Its only failure is the database being unreachable (500 internal_error).
    response={200: SetupStatusOut, 500: ErrorOut},
    auth=None,
    operation_id="getSetupStatus",
)
def get_setup_status(request: HttpRequest) -> SetupStatusOut:
    return SetupStatusOut(needs_setup=services.needs_setup())


@router.post(
    "/setup",
    response={201: SetupOut, 409: ErrorOut, 422: ErrorOut},
    auth=None,
    operation_id="setupServer",
)
def setup_server(request: HttpRequest, payload: SetupIn) -> Status[SetupOut]:
    membership = services.setup_server(request, **payload.model_dump())
    user = UserOut.model_validate(membership.user, from_attributes=True)
    return Status(201, SetupOut(user=user, workspace=WorkspaceOut.from_membership(membership)))


@router.post(
    "/auth/login",
    response={200: UserOut, 401: ErrorOut, 422: ErrorOut, 429: ErrorOut},
    auth=None,
    operation_id="login",
)
def login(request: HttpRequest, payload: LoginIn) -> User:
    return services.login(request, email=payload.email, password=payload.password)


@router.post(
    "/auth/logout",
    response={204: None, 401: ErrorOut, 403: ErrorOut},
    auth=django_auth,
    operation_id="logout",
)
def logout(request: HttpRequest) -> Status[None]:
    services.logout(request)
    return Status(204, None)


@router.get("/me", response={200: UserOut, 401: ErrorOut}, operation_id="getMe")
def get_me(request: HttpRequest) -> User:
    return services.current_user(request)

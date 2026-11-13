from django.http import HttpRequest
from ninja import Router

from apps.accounts.services import current_user
from apps.common.schemas import ErrorOut
from apps.workspaces import services
from apps.workspaces.schemas import WorkspaceOut

router = Router(tags=["workspaces"])


@router.get(
    "/workspaces",
    response={200: list[WorkspaceOut], 401: ErrorOut},
    operation_id="listWorkspaces",
)
def list_workspaces(request: HttpRequest) -> list[WorkspaceOut]:
    memberships = services.list_memberships(current_user(request))
    return [WorkspaceOut.from_membership(m) for m in memberships]

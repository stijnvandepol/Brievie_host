from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from apps.accounts.services import current_user
from apps.common.schemas import AUTH_ERRORS, ErrorOut
from apps.workspaces import services
from apps.workspaces.deps import workspace_member
from apps.workspaces.schemas import MemberOut, WorkspaceOut

router = Router(tags=["workspaces"])


@router.get(
    "/workspaces",
    response={200: list[WorkspaceOut], **AUTH_ERRORS},
    operation_id="listWorkspaces",
)
def list_workspaces(request: HttpRequest) -> list[WorkspaceOut]:
    memberships = services.list_memberships(current_user(request))
    return [WorkspaceOut.from_membership(m) for m in memberships]


@router.get(
    "/workspaces/{workspace_id}/members",
    response={200: list[MemberOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listMembers",
)
def list_members(request: HttpRequest, workspace_id: UUID) -> list[MemberOut]:
    membership = workspace_member(request, workspace_id)
    return services.list_members(membership.workspace, viewer=current_user(request))

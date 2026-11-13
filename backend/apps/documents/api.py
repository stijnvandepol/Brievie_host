from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.documents import services
from apps.documents.models import Folder
from apps.documents.schemas import FolderIn, FolderOut
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["documents"])


@router.get(
    "/workspaces/{workspace_id}/folders",
    response={200: list[FolderOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listFolders",
)
def list_folders(request: HttpRequest, workspace_id: UUID) -> list[Folder]:
    membership = workspace_member(request, workspace_id)
    return services.list_folders(membership.workspace)


@router.post(
    "/workspaces/{workspace_id}/folders",
    response={201: FolderOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="createFolder",
)
def create_folder(request: HttpRequest, workspace_id: UUID, payload: FolderIn) -> Status[Folder]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    folder = services.create_folder(
        workspace=membership.workspace, name=payload.name, parent_id=payload.parent_id
    )
    return Status(201, folder)

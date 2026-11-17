from uuid import UUID

from django.http import HttpRequest
from ninja import Router

from apps.common.schemas import AUTH_ERRORS, ErrorOut
from apps.pipeline import settings_service
from apps.pipeline.schemas import AiStatusOut
from apps.workspaces.deps import workspace_member

router = Router(tags=["ai"])


@router.get(
    "/workspaces/{workspace_id}/ai",
    response={200: AiStatusOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getAiStatus",
    description=(
        "How the workspace's letters are read: `off`, `external` or `local`. "
        "No address, model or key."
    ),
)
def get_ai_status(request: HttpRequest, workspace_id: UUID) -> AiStatusOut:
    workspace_member(request, workspace_id)
    return AiStatusOut(mode=settings_service.ai_mode(workspace_id))

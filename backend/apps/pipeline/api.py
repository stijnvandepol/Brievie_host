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
        "How the workspace's letters are read: `off`, `external` or `local`. For "
        "`external` also the supplier, the host the text goes to and since when "
        "(the moment of consent). No full address, model or key."
    ),
)
def get_ai_status(request: HttpRequest, workspace_id: UUID) -> AiStatusOut:
    workspace_member(request, workspace_id)
    status = settings_service.status(workspace_id)
    return AiStatusOut(
        mode=status.mode, supplier=status.supplier, host=status.host, since=status.since
    )

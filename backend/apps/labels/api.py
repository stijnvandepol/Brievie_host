from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from apps.audit.services import actor_from_request
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.labels import presets, services
from apps.labels.models import Label
from apps.labels.schemas import LabelIn, LabelOut, LabelUpdateIn
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["labels"])

_LABELS = "/workspaces/{workspace_id}/labels"
_LABEL = "/workspaces/{workspace_id}/labels/{label_id}"


@router.get(
    _LABELS,
    response={200: list[LabelOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listLabels",
)
def list_labels(request: HttpRequest, workspace_id: UUID) -> list[Label]:
    membership = workspace_member(request, workspace_id)
    return services.list_labels(membership.workspace)


@router.post(
    _LABELS,
    response={201: LabelOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="createLabel",
)
def create_label(request: HttpRequest, workspace_id: UUID, payload: LabelIn) -> Status[Label]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    label = services.create_label(
        workspace=membership.workspace, name=payload.name, color=payload.color
    )
    return Status(201, label)


@router.post(
    f"{_LABELS}/preset",
    response={200: list[LabelOut], 404: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="applyLabelPreset",
    description=(
        "Create the start set's labels that are missing (matched on `system_key`, then "
        "on name); existing labels stay as they are. Returns all labels."
    ),
)
def apply_label_preset(request: HttpRequest, workspace_id: UUID) -> list[Label]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.ADMIN)
    return presets.apply_preset(membership.workspace)


@router.put(
    _LABEL,
    response={200: LabelOut, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS, **BODY_ERRORS},
    operation_id="updateLabel",
)
def update_label(
    request: HttpRequest, workspace_id: UUID, label_id: UUID, payload: LabelUpdateIn
) -> Label:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    return services.update_label(
        workspace=membership.workspace, label_id=label_id, name=payload.name, color=payload.color
    )


@router.delete(
    _LABEL,
    response={204: None, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="deleteLabel",
    description=(
        "Also removes the label from its documents (their history keeps its name) and its "
        "preset rule. 409 `label_in_use` when another rule names the label."
    ),
)
def delete_label(request: HttpRequest, workspace_id: UUID, label_id: UUID) -> Status[None]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    services.delete_label(
        workspace=membership.workspace, label_id=label_id, actor=actor_from_request(request)
    )
    return Status(204, None)

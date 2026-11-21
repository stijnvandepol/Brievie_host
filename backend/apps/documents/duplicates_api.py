"""Duplicate letters over the API: the open pairs a member sees in full, merge and dismiss
(spec `docs/superpowers/specs/2026-11-20-duplicates-design.md`, "API")."""

from uuid import UUID

from django.http import HttpRequest
from ninja import Router, Status

from apps.accounts.services import current_user
from apps.audit.services import actor_from_request
from apps.common.schemas import AUTH_ERRORS, BODY_ERRORS, SESSION_WRITE_ERRORS, ErrorOut
from apps.documents import duplicates, services
from apps.documents.schemas import DocumentOut, DuplicateOut, MergeDuplicateIn
from apps.workspaces.deps import workspace_member
from apps.workspaces.models import Membership

router = Router(tags=["duplicates"])

_SEEN = (
    " Only a pair whose two letters the caller both sees; any other answers as a missing"
    " one (404 `duplicate_not_found`)."
)


@router.get(
    "/workspaces/{workspace_id}/duplicates",
    response={200: list[DuplicateOut], 404: ErrorOut, **AUTH_ERRORS},
    operation_id="listDuplicates",
    description=(
        "The open pairs of letters that are probably one letter scanned twice, newest first"
        " (at most 100). A viewer gets an empty list." + _SEEN
    ),
)
def list_duplicates(request: HttpRequest, workspace_id: UUID) -> list[DuplicateOut]:
    membership = workspace_member(request, workspace_id)
    return duplicates.list_duplicates(membership)


@router.get(
    "/workspaces/{workspace_id}/duplicates/{duplicate_id}",
    response={200: DuplicateOut, 404: ErrorOut, **AUTH_ERRORS},
    operation_id="getDuplicate",
    description=(
        "One open pair with what is the same and what differs. 404 `duplicate_not_found` for"
        " a decided pair and for a viewer." + _SEEN
    ),
)
def get_duplicate(request: HttpRequest, workspace_id: UUID, duplicate_id: UUID) -> DuplicateOut:
    membership = workspace_member(request, workspace_id)
    return duplicates.get_duplicate(membership=membership, candidate_id=duplicate_id)


@router.post(
    "/workspaces/{workspace_id}/duplicates/{duplicate_id}/merge",
    response={
        200: DocumentOut,
        404: ErrorOut,
        409: ErrorOut,
        **SESSION_WRITE_ERRORS,
        **BODY_ERRORS,
    },
    operation_id="mergeDuplicate",
    description=(
        "The two letters are one (role member or admin; a viewer gets 403). The kept letter"
        " gets the other's labels, addressees and open tasks; with `complete_twin_tasks` a"
        " task done on the other letter completes its open equal on the kept one"
        " (`task_twins`). The other letter goes to the trash (`restoreDocument` takes it"
        " back for 30 days; the merge itself is not undone). Answers the kept letter."
        " 409 `duplicate_not_open` when already decided, `duplicate_visibility_differs`"
        " when the letters are not seen by the same people (`can_merge`),"
        " `document_processing`, `document_retained`; 422 `validation_error` for a"
        " `keep_document_id` outside the pair. Written to the log (`document_trashed`,"
        " reason `duplicate`)." + _SEEN
    ),
)
def merge_duplicate(
    request: HttpRequest, workspace_id: UUID, duplicate_id: UUID, payload: MergeDuplicateIn
) -> DocumentOut:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    document = duplicates.merge(
        membership=membership,
        user=current_user(request),
        actor=actor_from_request(request),
        candidate_id=duplicate_id,
        keep_id=payload.keep_document_id,
        complete_twin_tasks=payload.complete_twin_tasks,
    )
    return services.document_out_as(membership, document)


@router.post(
    "/workspaces/{workspace_id}/duplicates/{duplicate_id}/dismiss",
    response={204: None, 404: ErrorOut, 409: ErrorOut, **SESSION_WRITE_ERRORS},
    operation_id="dismissDuplicate",
    description=(
        "The two letters are two letters (role member or admin; a viewer gets 403): the pair"
        " is never proposed again. 409 `duplicate_not_open` when already decided." + _SEEN
    ),
)
def dismiss_duplicate(request: HttpRequest, workspace_id: UUID, duplicate_id: UUID) -> Status[None]:
    membership = workspace_member(request, workspace_id, min_role=Membership.Role.MEMBER)
    duplicates.dismiss(membership=membership, user=current_user(request), candidate_id=duplicate_id)
    return Status(204, None)

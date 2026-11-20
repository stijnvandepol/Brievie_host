"""The trash: moving a letter to it and back, and reading it.

A letter in the trash keeps everything (status, labels, addressees, suggestions, tasks,
readers); only `trashed_at` and `trashed_by` say where it is, and every read leaves it
out (`visibility.visible_documents`, `models.LIVE`). These paths run as the caller: what
a member trashes, restores or lists starts from `visible_documents` or
`trashed_documents`, so a letter they may not see answers as a missing one.
"""

from datetime import timedelta
from typing import Final
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.documents import organise, retention, visibility
from apps.documents.models import Document
from apps.documents.schemas import TrashedDocumentOut
from apps.documents.services import HEADLINE_PROPOSALS, SUGGESTIONS, document_summary_out
from apps.pipeline.jobs import defer_processing, has_live_job
from apps.workspaces.locks import lock_own_membership
from apps.workspaces.models import Membership

# How long a letter stays in the trash before it is purged (a constant, ruling 9).
TRASH_RETENTION: Final = timedelta(days=30)
# The most letters `listTrash` answers with, newest first.
TRASH_LIST_LIMIT = 500


def _not_found() -> ApiError:
    return ApiError(404, "document_not_found", _("We kunnen dit document niet vinden."))


def move_to_trash(
    document: Document, *, user: User | None, actor: AuditActor, reason: str = "user"
) -> None:
    """Put a locked live letter in the trash: the two fields, a history line and an audit
    row with `reason`. Nothing else about the letter changes. The building block for
    every caller (a later duplicate merge passes `reason="duplicate"`)."""
    with transaction.atomic():
        verdict = retention.check_removal(document, step="trash")
        if not verdict.allowed:
            raise retention.retained_error(verdict)
        document.trashed_at = timezone.now()
        document.trashed_by = user
        document.save(update_fields=["trashed_at", "trashed_by", "updated_at"])
        organise.record_trashed(document, actor=user)
        record(
            AuditCode.DOCUMENT_TRASHED,
            actor=actor,
            target=AuditTarget(TargetKind.DOCUMENT, document.id),
            workspace_id=document.workspace_id,
            values={"reason": reason},
        )


def trash_document(
    *, membership: Membership, user: User, actor: AuditActor, document_id: UUID
) -> Document:
    """Move a letter the member sees to the trash. One already in their trash is answered
    unchanged (a double click); anything else is not found."""
    with transaction.atomic():
        lock_own_membership(membership.workspace_id, user.id)
        document = (
            visibility.visible_documents(membership)
            .select_for_update(no_key=True)
            .filter(id=document_id)
            .first()
        )
        if document is None:
            already = (
                visibility.trashed_documents(membership)
                .select_related("trashed_by")
                .filter(id=document_id)
                .first()
            )
            if already is None:
                raise _not_found()
            return already
        move_to_trash(document, user=user, actor=actor)
    return document


def restore_document(
    *, membership: Membership, user: User, actor: AuditActor, document_id: UUID
) -> Document:
    """Put a letter back from the member's trash, as it was. One the member sees already
    is answered unchanged; one whose purge started, or that they may not see, is not
    found. A letter still `processing` without a job is queued again."""
    with transaction.atomic():
        lock_own_membership(membership.workspace_id, user.id)
        document = (
            visibility.trashed_documents(membership)
            .select_for_update(no_key=True)
            .filter(id=document_id)
            .first()
        )
        if document is None:
            live = visibility.visible_documents(membership).filter(id=document_id).first()
            if live is None:
                raise _not_found()
            return live
        document.trashed_at = None
        document.trashed_by = None
        document.save(update_fields=["trashed_at", "trashed_by", "updated_at"])
        organise.record_restored(document, actor=user)
        record(
            AuditCode.DOCUMENT_RESTORED,
            actor=actor,
            target=AuditTarget(TargetKind.DOCUMENT, document.id),
            workspace_id=document.workspace_id,
        )
        if document.status == Document.Status.PROCESSING and not has_live_job(document.id):
            defer_processing(workspace_id=document.workspace_id, document_id=document.id)
    return document


def list_trash(membership: Membership) -> list[Document]:
    """The letters in the member's trash, the most recently trashed first (at most
    `TRASH_LIST_LIMIT`)."""
    return list(
        visibility.trashed_documents(membership)
        .select_related("trashed_by")
        .prefetch_related(
            "addressee_links",
            "label_links",
            SUGGESTIONS,
            HEADLINE_PROPOSALS,
        )
        .order_by("-trashed_at", "-id")[:TRASH_LIST_LIMIT]
    )


def trashed_out(document: Document) -> TrashedDocumentOut:
    """A row of the trash: the summary, when and by whom, and when it is purged."""
    trashed_at = document.trashed_at
    if trashed_at is None:
        raise ValueError("Only a letter in the trash has a trash row.")
    by = document.trashed_by
    return TrashedDocumentOut(
        **document_summary_out(document).model_dump(),
        trashed_at=trashed_at,
        trashed_by_name=by.name if by is not None else "",
        purge_after=trashed_at + TRASH_RETENTION,
    )

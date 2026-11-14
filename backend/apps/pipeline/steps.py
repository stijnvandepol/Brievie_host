"""The pipeline: a row of small steps from stored text to proposals for review."""

import logging
from uuid import UUID

from apps.documents.models import Document
from apps.workspaces.context import workspace_context

logger = logging.getLogger(__name__)


def run_pipeline(workspace_id: UUID, document_id: UUID) -> None:
    """Process one document. Safe to run twice: only a `processing` document is touched.

    Every database step has its own short workspace context; no transaction stays
    open while a slow step (the AI call) runs.
    """
    with workspace_context(workspace_id):
        if _lock_processing(document_id) is None:
            logger.info("Skipped document %s in workspace %s", document_id, workspace_id)
            return

    with workspace_context(workspace_id):
        document = _lock_processing(document_id)
        if document is None:
            return
        document.status = Document.Status.NEEDS_REVIEW
        document.save(update_fields=["status", "updated_at"])


def mark_failed(workspace_id: UUID, document_id: UUID, code: str) -> None:
    """Record that processing failed. Only a `processing` document is touched."""
    with workspace_context(workspace_id):
        document = _lock_processing(document_id)
        if document is None:
            return
        document.status = Document.Status.FAILED
        document.failure_code = code
        document.save(update_fields=["status", "failure_code", "updated_at"])
    logger.warning("Document %s failed: %s", document_id, code)


def _lock_processing(document_id: UUID) -> Document | None:
    """The document, locked, when it exists in this workspace and is still processing."""
    return (
        Document.objects.select_for_update()
        .filter(id=document_id, status=Document.Status.PROCESSING)
        .first()
    )

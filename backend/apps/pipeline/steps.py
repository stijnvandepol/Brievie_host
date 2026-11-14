"""The pipeline: a row of small steps from stored text to proposals for review.

Each step has its own input and output and may run again without harm: a job is
retried after a failure, and a document that is no longer `processing` is left alone.
"""

import logging
import time
from collections.abc import Callable
from datetime import date
from uuid import UUID

from django.utils import timezone
from pydantic import ValidationError

from apps.common.privacy import mask_bsns
from apps.documents.models import Document, Extraction
from apps.pipeline.errors import PermanentError
from apps.pipeline.providers import LLMProvider
from apps.pipeline.schema import ExtractionPayload
from apps.pipeline.settings_service import provider_for
from apps.pipeline.validation import ValidatedValue, validate
from apps.workspaces.context import workspace_context

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[UUID], LLMProvider | None]


def run_pipeline(
    workspace_id: UUID,
    document_id: UUID,
    *,
    provider_factory: ProviderFactory = provider_for,
    today: Callable[[], date] = timezone.localdate,
) -> None:
    """Process one document. Safe to run twice: only a `processing` document is touched.

    Every database step has its own short workspace context; no transaction stays
    open while the AI call runs.
    """
    started = time.monotonic()
    with workspace_context(workspace_id):
        document = _lock_processing(document_id)
        if document is None:
            logger.info("Skipped document %s in workspace %s", document_id, workspace_id)
            return
        text = load_text(document)
        provider = provider_factory(workspace_id)

    values: list[ValidatedValue] = []
    if provider is not None:
        payload = extract(provider, text)
        values = validate(payload, text, today=today())

    with workspace_context(workspace_id):
        document = _lock_processing(document_id)
        if document is None:
            # Confirmed, failed or deleted while the AI was reading: leave it.
            logger.info("Document %s changed during processing; result dropped", document_id)
            return
        store_proposals(document, values, provider)
    logger.info(
        "Processed document %s: %d proposals in %.1f s",
        document_id,
        len(values),
        time.monotonic() - started,
    )


def load_text(document: Document) -> str:
    """The document's text for the AI, masked once more: no BSN may leave the server."""
    return mask_bsns(document.ocr_text)


def extract(provider: LLMProvider, text: str) -> ExtractionPayload:
    """Ask the provider and parse its answer; one more try when the answer is unusable."""
    for _attempt in range(2):
        raw = provider.extract(text)
        try:
            return ExtractionPayload.model_validate_json(_strip_code_fence(raw))
        except ValidationError:
            # Its text repeats the input, so it is neither logged nor passed on.
            continue
    raise PermanentError("ai_invalid_output") from None


def store_proposals(
    document: Document, values: list[ValidatedValue], provider: LLMProvider | None
) -> None:
    """Replace the document's proposals and offer it for review."""
    document.extractions.all().delete()
    for value in values:
        Extraction.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            field=value.field,
            group=value.group,
            value=mask_bsns(value.value),
            quote=mask_bsns(value.quote),
            confidence=value.confidence,
            provider=provider.name if provider is not None else "",
            model=provider.model if provider is not None else "",
        )
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


def _strip_code_fence(raw: str) -> str:
    """Some models wrap their JSON in a Markdown code fence."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()

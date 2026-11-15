"""Background jobs (Procrastinate). Thin: arguments are ids, the work is in `steps`."""

import logging
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

from django.utils import timezone
from procrastinate import JobContext, RetryStrategy
from procrastinate.contrib.django import app
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.documents.models import Document
from apps.pipeline.errors import PermanentError, RetryableError
from apps.pipeline.steps import mark_failed, run_pipeline
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace

logger = logging.getLogger(__name__)

QUEUE = "pipeline"
MAX_ATTEMPTS = 3
# Longer than a job with all its retries can take (about 8 minutes).
ABANDONED_AFTER = timedelta(minutes=15)

Runner = Callable[[UUID, UUID], None]


def run_job(
    workspace_id: UUID, document_id: UUID, *, attempts: int, run: Runner = run_pipeline
) -> None:
    """Run the pipeline and make sure the document never stays in `processing`.

    `attempts` is the number of earlier attempts. A retryable error is raised again
    (the queue retries) until the last attempt; then, and for any other failure, the
    document is marked failed with a code.
    """
    try:
        run(workspace_id, document_id)
    except RetryableError as exc:
        if attempts + 1 < MAX_ATTEMPTS:
            raise
        mark_failed(workspace_id, document_id, exc.code)
    except PermanentError as exc:
        mark_failed(workspace_id, document_id, exc.code)
    except Exception as exc:  # noqa: BLE001 - any failure must end in a visible status
        # The type only: an exception message may quote the document.
        logger.error("Unexpected %s while processing %s", type(exc).__name__, document_id)
        mark_failed(workspace_id, document_id, "internal_error")


@app.task(
    name="process_document",
    queue=QUEUE,
    pass_context=True,
    # Waits 10 s, then 100 s.
    retry=RetryStrategy(
        max_attempts=MAX_ATTEMPTS, exponential_wait=10, retry_exceptions=[RetryableError]
    ),
)
def process_document(context: JobContext, *, workspace_id: str, document_id: str) -> None:
    run_job(UUID(workspace_id), UUID(document_id), attempts=context.job.attempts)


def defer_processing(*, workspace_id: UUID, document_id: UUID) -> None:
    """Queue the pipeline for a document, inside the caller's transaction."""
    process_document.defer(workspace_id=str(workspace_id), document_id=str(document_id))


@app.periodic(cron="*/5 * * * *")
@app.task(name="retry_stalled_jobs", queue=QUEUE, pass_context=True)
async def retry_stalled_jobs(context: JobContext, timestamp: int) -> None:
    """Give jobs of a worker that died back to the queue, so no document hangs."""
    for job in await context.app.job_manager.get_stalled_jobs(queue=QUEUE):
        await context.app.job_manager.retry_job(job)


def requeue_abandoned_documents() -> int:
    """Queue documents again that are `processing` without a waiting or running job.

    That happens when recording a failure itself failed (the database went away):
    the job is over and nothing else would ever move the document. Returns how many
    were queued.
    """
    stale_before = timezone.now() - ABANDONED_AFTER
    queued = 0
    for workspace_id in Workspace.objects.values_list("id", flat=True):
        with workspace_context(workspace_id):
            stale = Document.objects.filter(
                status=Document.Status.PROCESSING, updated_at__lt=stale_before
            ).values_list("id", flat=True)
            for document_id in stale:
                if _has_live_job(document_id):
                    continue
                defer_processing(workspace_id=workspace_id, document_id=document_id)
                queued += 1
    if queued:
        logger.warning("Queued %d abandoned documents again", queued)
    return queued


def _has_live_job(document_id: UUID) -> bool:
    return ProcrastinateJob.objects.filter(
        task_name="process_document",
        status__in=["todo", "doing"],
        args__document_id=str(document_id),
    ).exists()


@app.periodic(cron="*/5 * * * *")
@app.task(name="requeue_abandoned_documents", queue=QUEUE)
def requeue_abandoned_documents_job(timestamp: int) -> None:
    requeue_abandoned_documents()

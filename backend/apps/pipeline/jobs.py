"""Background jobs (Procrastinate). Thin: arguments are ids, the work is in `steps`."""

import logging
from collections.abc import Callable
from uuid import UUID

from procrastinate import JobContext, RetryStrategy
from procrastinate.contrib.django import app

from apps.pipeline.errors import PermanentError, RetryableError
from apps.pipeline.steps import mark_failed, run_pipeline

logger = logging.getLogger(__name__)

QUEUE = "pipeline"
MAX_ATTEMPTS = 3

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

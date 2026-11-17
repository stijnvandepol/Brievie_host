"""Background jobs of the back-up (Procrastinate queue `backup`, served by the keeper)."""

from uuid import UUID

from procrastinate import JobContext
from procrastinate.contrib.django import app

from apps.backup import services

QUEUE = "backup"


# lock: one back-up job at a time. No retry: a failed back-up is a visible status,
# and the next one is requested by the schedule or the owner.
@app.task(name="run_backup", queue=QUEUE, lock="backup")
def run_backup(*, run_id: str, clean_up_run_ids: list[str] | None = None) -> None:
    """`clean_up_run_ids`: abandoned runs whose snapshots this run removes first."""
    services.perform_run(UUID(run_id), clean_up=[UUID(i) for i in clean_up_run_ids or ()])


@app.periodic(cron="*/5 * * * *")
# Every worker defers every periodic task; the queueing lock keeps at most one
# waiting when no keeper serves the queue (the deferrer skips AlreadyEnqueued).
@app.task(
    name="retry_stalled_backup_jobs",
    queue=QUEUE,
    pass_context=True,
    queueing_lock="retry_stalled_backup_jobs",
)
async def retry_stalled_backup_jobs(context: JobContext, timestamp: int) -> None:
    """Give jobs of a keeper that died back to the queue; the retry resumes the `running` run."""
    for job in await context.app.job_manager.get_stalled_jobs(queue=QUEUE):
        await context.app.job_manager.retry_job(job)

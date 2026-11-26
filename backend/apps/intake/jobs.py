"""The scan folder's jobs (Procrastinate, queue `intake`): only the `intake` service, which
alone mounts the share, serves this queue."""

from procrastinate import JobContext
from procrastinate.contrib.django import app
from procrastinate.jobs import Status

from apps.intake.scan_folder import scan_round

QUEUE = "intake"


@app.periodic(cron="*/1 * * * *")
# Every worker defers every periodic task; the queueing lock keeps at most one waiting
# (a server without an `intake` service gathers no pile), and the lock lets one run at a time.
@app.task(name="scan_folders", queue=QUEUE, queueing_lock="scan_folders", lock="scan_folders")
def scan_folders(timestamp: int) -> None:
    scan_round()


@app.periodic(cron="*/5 * * * *")
# Without the round's lock: a round of a worker that was killed stays `doing` and holds that
# lock, so no later round would start (security review of PR #64, H1). The stalled round is
# closed as failed, not retried: the next round already waits with the same queueing lock,
# and a retry would be a second `todo` row with it (the unique index refuses). Closed, the
# lock frees and the waiting round runs; a round is periodic and safe to lose.
@app.task(
    name="retry_stalled_intake_jobs",
    queue=QUEUE,
    pass_context=True,
    queueing_lock="retry_stalled_intake_jobs",
)
async def retry_stalled_intake_jobs(context: JobContext, timestamp: int) -> None:
    manager = context.app.job_manager
    for job in await manager.get_stalled_jobs(queue=QUEUE):
        if job.task_name == "scan_folders":
            await manager.finish_job(job, Status.FAILED, delete_job=False)
        else:
            await manager.retry_job(job)

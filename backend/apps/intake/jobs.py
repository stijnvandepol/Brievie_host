"""The scan folder's jobs (Procrastinate, queue `intake`): only the `intake` service, which
alone mounts the share, serves this queue."""

from procrastinate import JobContext
from procrastinate.contrib.django import app

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
# lock, so no later round would start (security review of PR #64, H1). Given back, the lock
# frees and the round runs again; it is safe to repeat.
@app.task(
    name="retry_stalled_intake_jobs",
    queue=QUEUE,
    pass_context=True,
    queueing_lock="retry_stalled_intake_jobs",
)
async def retry_stalled_intake_jobs(context: JobContext, timestamp: int) -> None:
    for job in await context.app.job_manager.get_stalled_jobs(queue=QUEUE):
        await context.app.job_manager.retry_job(job)

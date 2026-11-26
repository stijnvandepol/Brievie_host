"""The scan folder's job (Procrastinate, queue `intake`): only the `intake` service, which
alone mounts the share, serves this queue."""

from procrastinate.contrib.django import app

from apps.intake.scan_folder import scan_round

QUEUE = "intake"


@app.periodic(cron="*/1 * * * *")
# Every worker defers every periodic task; the queueing lock keeps at most one waiting
# (a server without an `intake` service gathers no pile), and the lock lets one run at a time.
@app.task(name="scan_folders", queue=QUEUE, queueing_lock="scan_folders", lock="scan_folders")
def scan_folders(timestamp: int) -> None:
    scan_round()

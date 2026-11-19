"""Background jobs of the member export. Thin: ids in, the work is in `member_export`.

Both run on the pipeline queue at a priority below a letter's processing, so an export
of a large workspace never keeps a letter waiting.
"""

from uuid import UUID

from procrastinate.contrib.django import app

from apps.pipeline.jobs import QUEUE

EXPORT_PRIORITY = -1


@app.task(name="build_member_export", queue=QUEUE, priority=EXPORT_PRIORITY)
def build_member_export(*, workspace_id: str, export_id: str) -> None:
    from apps.documents import member_export

    member_export.build_export(UUID(workspace_id), UUID(export_id))


@app.periodic(cron="*/30 * * * *")
# Every worker defers every periodic task; the queueing lock keeps at most one waiting.
@app.task(
    name="remove_expired_exports",
    queue=QUEUE,
    priority=EXPORT_PRIORITY,
    queueing_lock="remove_expired_exports",
)
def remove_expired_exports(timestamp: int) -> None:
    from apps.documents import member_export

    member_export.remove_expired()

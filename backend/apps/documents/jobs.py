"""Background jobs of the member export and the nightly visibility check. Thin: ids in,
the work is in `member_export` and `visibility`.

They run on the pipeline queue at a priority below a letter's processing, so an export
of a large workspace never keeps a letter waiting.
"""

import logging
from uuid import UUID

from procrastinate.contrib.django import app

from apps.pipeline.jobs import QUEUE

EXPORT_PRIORITY = -1

logger = logging.getLogger(__name__)


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


@app.periodic(cron="40 3 * * *")
@app.task(
    name="verify_visibility",
    queue=QUEUE,
    priority=EXPORT_PRIORITY,
    queueing_lock="verify_visibility",
)
def verify_visibility_job(timestamp: int) -> None:
    """The nightly check of the derived visibility (S 4.9.2, 4.9.4): counts only, never a
    name or an id."""
    from apps.documents import visibility

    check = visibility.verify_all()
    level = (
        logging.WARNING if check.addressee_ids_fixed or check.reader_rows_fixed else logging.INFO
    )
    logger.log(
        level,
        "Visibility check: %d addressee lists and %d reader rows differed",
        check.addressee_ids_fixed,
        check.reader_rows_fixed,
    )

"""Settle the Procrastinate queue a restored dump brought back (Procrastinate 3).

Every dump holds the job of the back-up that made it, fetched (`doing`) and holding
the task lock `backup`; until the stalled-job retry (every 5 minutes) gives it back,
no new back-up job with that lock is fetched. Right after pg_restore, before any
service starts, the restored queue is settled through Procrastinate's own SQL:

- jobs of the `backup` queue that wait or run become `failed`
  (`procrastinate_finish_job_v1`): their runs are closed as interrupted anyway;
- running jobs of other queues go back to `todo` at once
  (`procrastinate_retry_job_v2`), as the stalled-job retry would do later; the
  pipeline's jobs are made to be run again (that retry is their recovery);
- the restored workers (the old server's) are unregistered
  (`procrastinate_unregister_worker_v1`).
"""

from dataclasses import dataclass

import psycopg
from psycopg.rows import TupleRow

BACKUP_QUEUE = "backup"


@dataclass(frozen=True)
class QueueSettled:
    failed: int
    requeued: int
    workers: int


def settle_restored_queue(conn: psycopg.Connection[TupleRow]) -> QueueSettled:
    """Settle the restored queue on `conn` (in the caller's transaction)."""
    failed = conn.execute(
        "SELECT procrastinate_finish_job_v1(id, 'failed'::procrastinate_job_status, false)"
        " FROM procrastinate_jobs WHERE queue_name = %s AND status IN ('todo', 'doing')",
        [BACKUP_QUEUE],
    ).rowcount
    requeued = conn.execute(
        "SELECT procrastinate_retry_job_v2(id, now(), NULL, NULL, NULL)"
        " FROM procrastinate_jobs WHERE queue_name <> %s AND status = 'doing'",
        [BACKUP_QUEUE],
    ).rowcount
    workers = conn.execute(
        "SELECT procrastinate_unregister_worker_v1(id) FROM procrastinate_workers"
    ).rowcount
    return QueueSettled(failed=failed, requeued=requeued, workers=workers)

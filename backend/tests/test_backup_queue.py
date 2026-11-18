"""After a restore the queue the dump brought back is settled (Procrastinate 3 SQL)."""

import psycopg
import pytest
from psycopg.rows import TupleRow

from apps.backup.queue import settle_restored_queue
from apps.backup.restore import close_interrupted_runs

pytestmark = pytest.mark.django_db(transaction=True)


def defer(conn: psycopg.Connection[TupleRow], queue: str, task: str, lock: str | None) -> int:
    row = conn.execute(
        "SELECT (procrastinate_defer_jobs_v1(ARRAY[ROW(%s, %s, 0, %s, NULL, '{}'::jsonb, NULL)"
        "::procrastinate_job_to_defer_v1]))[1]",
        [queue, task, lock],
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


def fetch(conn: psycopg.Connection[TupleRow], queue: str) -> int | None:
    row = conn.execute(
        "SELECT (procrastinate_fetch_job_v2(ARRAY[%s]::varchar[], NULL)).id", [queue]
    ).fetchone()
    return None if row is None or row[0] is None else int(str(row[0]))


def status(conn: psycopg.Connection[TupleRow], job_id: int) -> str:
    row = conn.execute("SELECT status FROM procrastinate_jobs WHERE id = %s", [job_id]).fetchone()
    assert row is not None
    return str(row[0])


def test_a_restored_running_backup_job_no_longer_blocks_a_new_one(owner_conninfo: str) -> None:
    with psycopg.connect(owner_conninfo, autocommit=True) as conn:
        # What every dump holds: its own run_backup job, fetched (doing, lock "backup").
        old = defer(conn, "backup", "run_backup", "backup")
        assert fetch(conn, "backup") == old
        waiting = defer(conn, "backup", "run_backup", "backup")
        # The analysis: a new back-up job cannot be fetched while the old one is "doing".
        new = defer(conn, "backup", "run_backup", "backup")
        assert fetch(conn, "backup") is None

        settled = settle_restored_queue(conn)

        assert settled.failed == 3
        assert status(conn, old) == "failed"
        assert status(conn, waiting) == "failed"
        assert status(conn, new) == "failed"
        fresh = defer(conn, "backup", "run_backup", "backup")
        assert fetch(conn, "backup") == fresh  # fetchable at once


def test_other_queues_running_jobs_go_back_to_the_queue(owner_conninfo: str) -> None:
    with psycopg.connect(owner_conninfo, autocommit=True) as conn:
        running = defer(conn, "pipeline", "process_document", None)
        assert fetch(conn, "pipeline") == running
        waiting = defer(conn, "pipeline", "process_document", None)

        settled = settle_restored_queue(conn)

        assert settled.requeued == 1
        assert status(conn, running) == "todo"
        assert status(conn, waiting) == "todo"
        assert fetch(conn, "pipeline") == running


def test_restored_workers_are_unregistered(owner_conninfo: str) -> None:
    with psycopg.connect(owner_conninfo, autocommit=True) as conn:
        conn.execute("SELECT procrastinate_register_worker_v1()")
        assert settle_restored_queue(conn).workers >= 1
        row = conn.execute("SELECT count(*) FROM procrastinate_workers").fetchone()
        assert row is not None
        assert row[0] == 0


def test_closing_the_restored_runs_settles_the_queue_too(owner_conninfo: str) -> None:
    with psycopg.connect(owner_conninfo, autocommit=True) as conn:
        old = defer(conn, "backup", "run_backup", "backup")
        assert fetch(conn, "backup") == old
    close_interrupted_runs(owner_conninfo)
    with psycopg.connect(owner_conninfo, autocommit=True) as conn:
        assert status(conn, old) == "failed"
        fresh = defer(conn, "backup", "run_backup", "backup")
        assert fetch(conn, "backup") == fresh

"""The scan folder's jobs on the `intake` queue (intake plan Task 12; security review of
PR #64, H1: a killed worker must not leave the lock held for ever)."""

import asyncio
from unittest import mock

import psycopg
import pytest
from procrastinate import PsycopgConnector
from procrastinate.contrib.django import app as procrastinate_app
from procrastinate.manager import JobManager
from psycopg.rows import TupleRow

from apps.intake import jobs


def periodic() -> dict[str, str]:
    return {
        entry.task.name: entry.cron
        for entry in procrastinate_app.periodic_registry.periodic_tasks.values()
    }


def test_the_round_runs_every_minute_one_at_a_time() -> None:
    task = procrastinate_app.tasks["scan_folders"]
    assert task.queue == "intake"
    assert task.lock == "scan_folders"
    assert task.queueing_lock == "scan_folders"
    assert periodic()["scan_folders"] == "*/1 * * * *"


def test_a_stalled_intake_job_is_given_back() -> None:
    retry = procrastinate_app.tasks["retry_stalled_intake_jobs"]
    assert retry.queue == "intake"
    # Without the round's lock: it must run while a dead round still holds it.
    assert retry.lock is None
    assert retry.queueing_lock == "retry_stalled_intake_jobs"
    assert periodic()["retry_stalled_intake_jobs"] == "*/5 * * * *"


def test_a_stalled_other_intake_job_is_retried() -> None:
    stalled = mock.Mock(task_name="other")
    manager = mock.Mock()
    manager.get_stalled_jobs = mock.AsyncMock(return_value=[stalled])
    manager.retry_job = mock.AsyncMock()
    context = mock.Mock()
    context.app.job_manager = manager

    asyncio.run(jobs.retry_stalled_intake_jobs(context, timestamp=0))

    manager.get_stalled_jobs.assert_awaited_once_with(queue="intake")
    manager.retry_job.assert_awaited_once_with(stalled)


def defer_round(conn: psycopg.Connection[TupleRow]) -> int:
    row = conn.execute(
        "SELECT (procrastinate_defer_jobs_v1(ARRAY[ROW('intake', 'scan_folders', 0, "
        "'scan_folders', 'scan_folders', '{\"timestamp\": 0}'::jsonb, NULL)"
        "::procrastinate_job_to_defer_v1]))[1]"
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


async def run_retry_task(conninfo: str) -> None:
    """The task on the real queue, through its own connection (the suite's Django connection
    is not left open by a worker thread)."""
    connector = PsycopgConnector(conninfo=conninfo)
    await connector.open_async()
    try:
        context = mock.Mock()
        context.app.job_manager = JobManager(connector)
        await jobs.retry_stalled_intake_jobs(context, timestamp=0)
    finally:
        await connector.close_async()


def status(conn: psycopg.Connection[TupleRow], job_id: int) -> str:
    row = conn.execute("SELECT status FROM procrastinate_jobs WHERE id = %s", [job_id]).fetchone()
    assert row is not None
    return str(row[0])


@pytest.mark.django_db(transaction=True)
def test_a_killed_round_is_closed_so_the_waiting_round_runs(owner_conninfo: str) -> None:
    # The real queue: round A was fetched (`doing`) by a worker that died; the periodic defer
    # then queued round B behind A's lock. A retry of A would collide with B's queueing lock.
    with psycopg.connect(owner_conninfo, autocommit=True) as conn:
        conn.execute("TRUNCATE procrastinate_jobs, procrastinate_workers CASCADE")
        dead = defer_round(conn)
        fetched = conn.execute(
            "SELECT (procrastinate_fetch_job_v2(ARRAY['intake']::varchar[], NULL)).id"
        ).fetchone()
        assert fetched is not None
        assert int(str(fetched[0])) == dead
        waiting = defer_round(conn)
        blocked = conn.execute(
            "SELECT (procrastinate_fetch_job_v2(ARRAY['intake']::varchar[], NULL)).id"
        ).fetchone()
        assert blocked is None or blocked[0] is None  # B waits behind the dead A

        asyncio.run(run_retry_task(owner_conninfo))

        assert status(conn, dead) == "failed"
        assert status(conn, waiting) == "todo"
        again = conn.execute(
            "SELECT (procrastinate_fetch_job_v2(ARRAY['intake']::varchar[], NULL)).id"
        ).fetchone()
        assert again is not None
        assert int(str(again[0])) == waiting

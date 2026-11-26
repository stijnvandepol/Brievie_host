"""The scan folder's jobs on the `intake` queue (intake plan Task 12; security review of
PR #64, H1: a killed worker must not leave the lock held for ever)."""

import asyncio
from unittest import mock

import pytest
from procrastinate.contrib.django import app as procrastinate_app

from apps.intake import jobs


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: nothing here reads the database."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


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


def test_stalled_intake_jobs_are_retried() -> None:
    stalled = object()
    manager = mock.Mock()
    manager.get_stalled_jobs = mock.AsyncMock(return_value=[stalled])
    manager.retry_job = mock.AsyncMock()
    context = mock.Mock()
    context.app.job_manager = manager

    asyncio.run(jobs.retry_stalled_intake_jobs(context, timestamp=0))

    manager.get_stalled_jobs.assert_awaited_once_with(queue="intake")
    manager.retry_job.assert_awaited_once_with(stalled)

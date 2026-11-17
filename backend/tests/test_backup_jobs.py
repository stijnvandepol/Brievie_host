import asyncio
from unittest import mock
from uuid import UUID, uuid4

import pytest
from procrastinate.contrib.django import app as procrastinate_app

from apps.backup import jobs, services


def test_backup_tasks_are_registered_on_the_app() -> None:
    task = procrastinate_app.tasks["run_backup"]
    assert task.queue == "backup"
    assert task.lock == "backup"
    retry = procrastinate_app.tasks["retry_stalled_backup_jobs"]
    assert retry.queue == "backup"
    # Every worker defers every periodic task; the lock keeps at most one waiting
    # when no keeper serves the queue.
    assert retry.queueing_lock == "retry_stalled_backup_jobs"
    periodic = {
        entry.task.name: entry.cron
        for entry in procrastinate_app.periodic_registry.periodic_tasks.values()
    }
    assert periodic["retry_stalled_backup_jobs"] == "*/5 * * * *"


def test_run_backup_performs_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[UUID] = []
    monkeypatch.setattr(services, "perform_run", seen.append)
    run_id = uuid4()

    jobs.run_backup(run_id=str(run_id))

    assert seen == [run_id]


def test_stalled_backup_jobs_are_retried() -> None:
    stalled = object()
    manager = mock.Mock()
    manager.get_stalled_jobs = mock.AsyncMock(return_value=[stalled])
    manager.retry_job = mock.AsyncMock()
    context = mock.Mock()
    context.app.job_manager = manager

    asyncio.run(jobs.retry_stalled_backup_jobs(context, timestamp=0))

    manager.get_stalled_jobs.assert_awaited_once_with(queue="backup")
    manager.retry_job.assert_awaited_once_with(stalled)

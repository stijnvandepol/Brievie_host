"""The nightly back-up: asked for only with a destination and only when one is due."""

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone
from procrastinate.contrib.django.models import ProcrastinateJob
from pytest_django import Settings

from apps.backup import jobs, schedule
from apps.backup.errors import BackupError
from apps.backup.models import BackupRun


def succeeded(hours_ago: float, *, kind: str = "backup", destination_kind: str = "folder") -> None:
    finished = timezone.now() - timedelta(hours=hours_ago)
    BackupRun.objects.create(
        kind=kind,
        status=BackupRun.Status.SUCCEEDED,
        finished_at=finished,
        destination_kind=destination_kind,
    )


# --- the pure rule ---


def test_nightly_is_due_without_a_back_up_and_after_twenty_hours() -> None:
    now = timezone.now()
    assert schedule.nightly_due(now=now, last_success_at=None)
    assert schedule.nightly_due(now=now, last_success_at=now - timedelta(hours=20))
    assert not schedule.nightly_due(now=now, last_success_at=now - timedelta(hours=19, minutes=59))


# --- the job ---


def test_the_job_is_periodic_on_the_backup_queue_at_the_chosen_hour(settings: Settings) -> None:
    task = jobs.nightly_backup
    assert task.queue == "backup"
    assert task.queueing_lock == "nightly_backup"
    assert settings.BRIEVIE_BACKUP_HOUR == 2


def test_without_a_destination_nothing_is_requested() -> None:
    assert schedule.request_nightly() is None
    assert not ProcrastinateJob.objects.exists()
    assert not BackupRun.objects.exists()


def test_within_twenty_hours_of_a_back_up_nothing_is_requested(configured_folder: Path) -> None:
    succeeded(5)
    assert schedule.request_nightly() is None
    assert not ProcrastinateJob.objects.exists()


def test_a_back_up_by_hand_counts_for_the_night(configured_folder: Path) -> None:
    BackupRun.objects.create(
        kind="backup",
        status=BackupRun.Status.SUCCEEDED,
        requested_by="web",
        finished_at=timezone.now() - timedelta(hours=1),
        destination_kind="folder",
    )
    assert schedule.request_nightly() is None


def test_the_fallback_of_an_update_is_no_back_up_for_the_night(configured_folder: Path) -> None:
    succeeded(1, destination_kind="update")
    run = schedule.request_nightly()
    assert run is not None
    assert run.requested_by == "schedule"


def test_a_due_night_defers_one_run_once(configured_folder: Path) -> None:
    succeeded(30)
    first = schedule.request_nightly()
    second = schedule.request_nightly()
    assert first is not None
    assert second is not None
    assert first.id == second.id
    job = ProcrastinateJob.objects.get()
    assert job.task_name == "run_backup"
    assert job.queue_name == "backup"


def test_the_periodic_task_calls_the_schedule(configured_folder: Path) -> None:
    jobs.nightly_backup(timestamp=0)
    assert ProcrastinateJob.objects.filter(task_name="run_backup").count() == 1


def test_a_check_does_not_count_as_a_back_up(configured_folder: Path) -> None:
    succeeded(1, kind="check")
    assert schedule.request_nightly() is not None


# --- by hand ---


def test_a_manual_back_up_needs_a_destination() -> None:
    with pytest.raises(BackupError) as caught:
        schedule.request_manual()
    assert caught.value.code == "not_configured"


def test_a_manual_back_up_is_refused_while_one_runs(configured_folder: Path) -> None:
    schedule.request_manual()
    with pytest.raises(BackupError) as caught:
        schedule.request_manual()
    assert caught.value.code == "backup_running"
    assert ProcrastinateJob.objects.filter(task_name="run_backup").count() == 1


def test_a_manual_back_up_is_a_web_run(configured_folder: Path) -> None:
    run = schedule.request_manual()
    assert run.requested_by == "web"


# --- the status ---


def test_the_status_without_a_destination(settings: Settings) -> None:
    found = schedule.status()
    assert found.destination == "none"
    assert not found.late
    assert found.last_success_at is None
    assert not found.running


def test_the_status_of_a_folder_with_and_without_a_good_back_up(configured_folder: Path) -> None:
    assert schedule.status().late
    succeeded(2)
    found = schedule.status()
    assert found.destination == "folder"
    assert not found.late
    assert found.last_success_at is not None


def test_a_back_up_older_than_36_hours_is_late(configured_folder: Path) -> None:
    succeeded(40)
    assert schedule.status().late


def test_a_failure_after_the_last_success_is_shown_and_an_older_one_is_not(
    configured_folder: Path,
) -> None:
    old = BackupRun.objects.create(
        kind="backup",
        status=BackupRun.Status.FAILED,
        reason="Oud.",
        finished_at=timezone.now() - timedelta(days=3),
    )
    succeeded(30)
    assert schedule.status().last_failure_reason == ""
    BackupRun.objects.create(
        kind="backup",
        status=BackupRun.Status.FAILED,
        reason="De bestemming van de back-up is niet bereikbaar.",
        finished_at=timezone.now() - timedelta(hours=1),
    )
    assert schedule.status().last_failure_reason == (
        "De bestemming van de back-up is niet bereikbaar."
    )
    assert old.id is not None


def test_an_open_run_is_running(configured_folder: Path) -> None:
    schedule.request_manual()
    assert schedule.status().running

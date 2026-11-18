"""Making and checking a back-up: request, run, wait, status (Task 6)."""

import json
import logging
import stat
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, timedelta
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

import psycopg
import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, connection
from django.utils import timezone
from procrastinate.contrib.django.models import ProcrastinateJob
from psycopg.conninfo import make_conninfo
from pytest_django import Settings

from apps.backup import restic, services
from apps.backup.locks import BACKUP_LOCK_KEY
from apps.backup.manifest import MANIFEST_NAME, manifest_from_json
from apps.backup.models import BackupRun
from apps.workspaces.models import Workspace
from tests.backup_support import FakeBinary

DB_SIZE = 1234
FILES_SIZE = 5678


def summary(total: int) -> bytes:
    lines = [
        {"message_type": "status", "percent_done": 0.5},
        {"message_type": "summary", "total_bytes_processed": total},
    ]
    return b"".join(json.dumps(line).encode() + b"\n" for line in lines)


def db_prefix(run_id: UUID) -> tuple[str, ...]:
    return ("backup", "--json", "--host", restic.HOST, *restic.tags(run_id, "db"))


def files_prefix(run_id: UUID) -> tuple[str, ...]:
    return ("backup", "--json", "--host", restic.HOST, *restic.tags(run_id, "files"))


@pytest.fixture
def fake_pg_dump(fake_tool: Callable[[str], FakeBinary]) -> FakeBinary:
    pg_dump = fake_tool("pg_dump")
    pg_dump.respond(("--version",), stdout=b"pg_dump (PostgreSQL) 18.1\n")
    return pg_dump


def queued_run(kind: str = "backup") -> BackupRun:
    return BackupRun.objects.create(kind=kind, destination_kind="folder")


def without_snapshot(calls: list[list[str]]) -> list[list[str]]:
    """The calls with the dump's `--snapshot=<id>` left out (the id differs per run)."""
    return [[arg for arg in call if not arg.startswith("--snapshot=")] for call in calls]


def answer_both_backups(fake_restic: FakeBinary, run_id: UUID) -> None:
    fake_restic.respond(db_prefix(run_id), stdout=summary(DB_SIZE))
    fake_restic.respond(files_prefix(run_id), stdout=summary(FILES_SIZE))
    # Retention lists the installation's snapshots first (its clock, its half runs).
    fake_restic.respond(("snapshots", "--json", "--host"), stdout=b"[]")


def dump_call(run_id: UUID, *extra: str) -> list[str]:
    """The dump argv of a run as it ran: the run's own start as its time."""
    started = BackupRun.objects.get(id=run_id).started_at
    assert started is not None
    return restic.backup_stdin_argv(run_id, [*services.DUMP_COMMAND, *extra], at=started)


def files_call(run_id: UUID) -> list[str]:
    started = BackupRun.objects.get(id=run_id).started_at
    assert started is not None
    return restic.backup_files_argv(
        run_id, services.file_paths(), at=started, excludes=services.file_excludes()
    )


def no_snapshots(fake_restic: FakeBinary) -> None:
    fake_restic.respond(("snapshots", "--json", "--host"), stdout=b"[]")


@pytest.fixture(autouse=True)
def env_copy(settings: Settings) -> Path:
    """The .env copy a back-up needs (./brievie stores it); in the test's config dir."""
    config = Path(settings.BRIEVIE_BACKUP_CONFIG_DIR)
    config.mkdir(parents=True, exist_ok=True)
    (config / "env").write_text("BRIEVIE_HOSTNAME=brievie.local\n")
    return config / "env"


def retention(host: str = restic.HOST) -> list[list[str]]:
    return [
        restic.snapshots_host_argv(host),
        restic.forget_safety_argv(host),
        restic.forget_argv(host),
    ]


def _owner_conninfo() -> str:
    owner = django_settings.DATABASE_OWNER
    assert owner is not None
    return make_conninfo(
        dbname=str(connection.settings_dict["NAME"]),
        user=str(owner["USER"]),
        password=str(owner["PASSWORD"]),
        host=str(owner["HOST"]),
        port=str(owner["PORT"]),
    )


# --- pure values ---------------------------------------------------------------------------


def test_dump_command_never_enables_row_security() -> None:
    # pg_dump --enable-row-security dumps only the rows the role may see: a partial
    # back-up. The back-up role bypasses RLS instead.
    assert "--enable-row-security" not in services.DUMP_COMMAND
    assert services.DUMP_COMMAND == ("pg_dump", "--format=custom", "--no-password")


def test_file_paths_are_documents_authority_and_config(settings: Settings) -> None:
    settings.BRIEVIE_AUTHORITY_COPY_DIR = "/data/authority"
    settings.BRIEVIE_BACKUP_CONFIG_DIR = "/data/backup-config"
    assert services.file_paths() == [
        settings.BRIEVIE_STORAGE_ROOT,
        "/data/authority",
        "/data/backup-config",
    ]


# --- request_run ---------------------------------------------------------------------------


def test_request_run_reuses_an_open_run(configured_folder: Path) -> None:
    first = services.request_run("backup")
    second = services.request_run("backup")

    assert second.id == first.id
    assert ProcrastinateJob.objects.count() == 1
    assert first.status == BackupRun.Status.QUEUED
    assert first.destination_kind == "folder"


def test_request_run_of_another_kind_is_its_own_run(configured_folder: Path) -> None:
    backup = services.request_run("backup")
    check = services.request_run("check")

    assert backup.id != check.id
    assert ProcrastinateJob.objects.count() == 2


def test_request_run_after_a_finished_run_makes_a_new_one(configured_folder: Path) -> None:
    BackupRun.objects.create(kind="backup", status=BackupRun.Status.SUCCEEDED)
    run = services.request_run("backup")
    assert BackupRun.objects.count() == 2
    assert run.status == BackupRun.Status.QUEUED


def test_request_run_defers_on_the_backup_queue(configured_folder: Path) -> None:
    run = services.request_run("backup", requested_by="web")

    job = ProcrastinateJob.objects.get()
    assert job.queue_name == "backup"
    assert job.task_name == "run_backup"
    assert job.args == {"run_id": str(run.id)}
    assert run.requested_by == "web"


def test_request_run_with_a_bad_destination_still_records_the_run(settings: Settings) -> None:
    # The run fails later with the reason; the request itself never raises.
    settings.BRIEVIE_BACKUP_REPOSITORY = "ftp://elders"
    run = services.request_run("backup")
    assert run.destination_kind == ""


# --- perform_run ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_run_dumps_first_then_files_then_forgets(
    ws_a: Workspace,
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.started_at is not None
    assert without_snapshot(fake_restic.calls) == [
        restic.unlock_argv(),
        dump_call(run.id),
        files_call(run.id),
        *retention(),
    ]
    assert run.status == BackupRun.Status.SUCCEEDED
    assert run.size_bytes == DB_SIZE + FILES_SIZE
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.reason_code == ""

    manifest_path = Path(django_settings.BRIEVIE_BACKUP_CONFIG_DIR) / MANIFEST_NAME
    manifest = manifest_from_json(manifest_path.read_text(encoding="utf-8"))
    assert manifest.run_id == run.id
    assert ws_a.id in manifest.workspaces
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    # Written atomically: no temporary file is left next to it.
    assert sorted(p.name for p in manifest_path.parent.iterdir()) == ["env", MANIFEST_NAME]


@pytest.mark.django_db(transaction=True)
def test_dump_runs_with_the_backup_role_in_the_environment(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    dump_env = fake_restic.env_keys[1]
    for name in ("RESTIC_PASSWORD", "RESTIC_REPOSITORY", "PGPASSWORD", "PGUSER", "PGDATABASE"):
        assert name in dump_env
    # Only the dump gets the database credentials.
    assert "PGPASSWORD" not in fake_restic.env_keys[2]


@pytest.mark.django_db(transaction=True)
def test_check_runs_restic_check_only(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = queued_run("check")

    services.perform_run(run.id)

    assert fake_restic.calls == [restic.unlock_argv(), restic.check_argv()]
    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED
    assert run.size_bytes is None


@pytest.mark.django_db(transaction=True)
def test_the_safety_backup_of_a_restore_never_runs_retention(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = services.request_run("backup", requested_by="restore")
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    assert all(call[0] != "forget" for call in fake_restic.calls)
    assert files_call(run.id)[:7] == next(c for c in fake_restic.calls if "kind:files" in c)[:7]
    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED
    assert run.requested_by == "restore"


def test_backup_command_no_prune_requests_the_safety_backup_of_a_restore(
    configured_folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "wait_for_run", _finished(BackupRun.Status.SUCCEEDED))
    call_command("backup", "--no-prune")
    assert BackupRun.objects.get().requested_by == "restore"
    BackupRun.objects.all().delete()
    call_command("backup")
    assert BackupRun.objects.get().requested_by == "cli"
    with pytest.raises(CommandError):
        call_command("backup", "--check", "--no-prune")


@pytest.mark.django_db(transaction=True)
def test_append_only_never_forgets(
    settings: Settings,
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    settings.BRIEVIE_BACKUP_APPEND_ONLY = True
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    assert all(call[0] != "forget" for call in fake_restic.calls)
    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_failed_dump_records_no_files_snapshot(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stderr="pg_dump: error: connection failed", code=1)
    fake_restic.respond(files_prefix(run.id), stdout=summary(FILES_SIZE))

    services.perform_run(run.id)

    assert [call[:1] for call in fake_restic.calls] == [["unlock"], ["backup"]]
    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "restic_failed"
    assert run.reason == "Het back-upprogramma gaf een fout."
    assert run.finished_at is not None


@pytest.mark.django_db(transaction=True)
def test_a_host_name_a_restore_could_not_read_fails_the_back_up(
    settings: Settings,
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    # Loudly now, never as an unreadable back-up at restore time.
    settings.BRIEVIE_HOSTNAME = "brievie.local\nrun_id=forged"
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "hostname_unusable"
    # Refused while counting, before the dump: no snapshot was made.
    assert [call[0] for call in fake_restic.calls] == ["unlock"]


@pytest.mark.django_db(transaction=True)
def test_incomplete_files_backup_is_kept_and_retention_runs(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    # Exit 3: restic made the snapshot but could not read some files. One file that
    # stays unreadable must not mean that no back-up is ever kept.
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=summary(DB_SIZE))
    fake_restic.respond(files_prefix(run.id), stdout=summary(FILES_SIZE), code=3)
    no_snapshots(fake_restic)

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.started_at is not None
    assert without_snapshot(fake_restic.calls) == [
        restic.unlock_argv(),
        dump_call(run.id),
        files_call(run.id),
        *retention(),
    ]
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "files_incomplete"
    assert run.reason.startswith("De back-up is gemaakt, maar")
    assert run.size_bytes == DB_SIZE + FILES_SIZE


@pytest.mark.django_db(transaction=True)
def test_incomplete_files_backup_without_summary_has_no_size(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=summary(DB_SIZE))
    fake_restic.respond(files_prefix(run.id), code=3)

    services.perform_run(run.id)

    assert restic.forget_run_argv(run.id) not in fake_restic.calls
    run.refresh_from_db()
    assert run.reason_code == "files_incomplete"
    assert run.size_bytes is None


@pytest.mark.django_db(transaction=True)
def test_incomplete_files_backup_in_append_only_mode_forgets_nothing(
    settings: Settings,
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    settings.BRIEVIE_BACKUP_APPEND_ONLY = True
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=summary(DB_SIZE))
    fake_restic.respond(files_prefix(run.id), stdout=summary(FILES_SIZE), code=3)

    services.perform_run(run.id)

    assert all(call[0] != "forget" for call in fake_restic.calls)
    run.refresh_from_db()
    assert run.reason_code == "files_incomplete"


@pytest.mark.django_db(transaction=True)
def test_failed_retention_after_an_incomplete_back_up_keeps_its_reason(
    configured_folder: Path,
    caplog: pytest.LogCaptureFixture,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=summary(DB_SIZE))
    fake_restic.respond(files_prefix(run.id), stdout=summary(FILES_SIZE), code=3)
    no_snapshots(fake_restic)
    fake_restic.respond(tuple(restic.forget_safety_argv()), code=11)

    with caplog.at_level(logging.DEBUG):
        services.perform_run(run.id)

    run.refresh_from_db()
    assert run.reason_code == "files_incomplete"
    assert "repository_locked" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_failed_run_reason_and_log_hold_no_secret(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://nas:8000/b"
    settings.BRIEVIE_BACKUP_REST_USERNAME = "brievie-user"
    settings.BRIEVIE_BACKUP_REST_PASSWORD = "geheim"
    settings.BRIEVIE_BACKUP_PASSWORD = "een-lang-wachtwoord-2026"
    fake_restic.respond(
        ("unlock",),
        stderr=(
            "Fatal: unable to open repository at rest:https://nas:8000/b: "
            "Get https://brievie-user:geheim@nas:8000/b/config: password een-lang-wachtwoord-2026"
        ),
        code=1,
    )
    run = queued_run()

    with caplog.at_level(logging.DEBUG):
        services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "restic_failed"
    assert f"Backup run {run.id} failed: restic_failed" in caplog.text
    assert "Fatal" in caplog.text  # the redacted tail is logged
    for text in (run.reason, caplog.text):
        assert "geheim" not in text
        assert "een-lang-wachtwoord-2026" not in text
        assert "nas:8000" not in text
        assert "brievie-user" not in text


@pytest.mark.django_db(transaction=True)
def test_restic_stdout_is_never_logged(
    configured_folder: Path,
    caplog: pytest.LogCaptureFixture,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=b'{"message":"stdout-only-text"}\n', code=1)

    with caplog.at_level(logging.DEBUG):
        services.perform_run(run.id)

    assert "stdout-only-text" not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_no_secret_is_in_any_argv_of_a_whole_run(
    settings: Settings, tmp_path: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    settings.BRIEVIE_BACKUP_CONFIG_DIR = str(tmp_path / "config")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "env").write_text("A=1\n")
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://nas:8000/b"
    settings.BRIEVIE_BACKUP_REST_USERNAME = "rest-user-xyz"
    settings.BRIEVIE_BACKUP_REST_PASSWORD = "rest-pass-xyz"
    settings.BRIEVIE_BACKUP_S3_ACCESS_KEY_ID = "s3-key-xyz"
    settings.BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY = "s3-secret-xyz"
    settings.BRIEVIE_BACKUP_PASSWORD = "backup-pass-xyz-0123456789"
    database = django_settings.DATABASE_BACKUP
    assert database is not None
    db_password = str(database["PASSWORD"])
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED
    secrets = (
        "rest-user-xyz",
        "rest-pass-xyz",
        "s3-key-xyz",
        "s3-secret-xyz",
        "backup-pass-xyz-0123456789",
        db_password,
    )
    calls = fake_restic.calls + fake_pg_dump.calls
    # pg_dump --version, unlock, two backups, the listing, two forgets
    assert len(calls) == 7
    for argv in calls:
        joined = " ".join(argv)
        for secret in secrets:
            assert secret not in joined
    assert "RESTIC_REST_PASSWORD" in fake_restic.env_keys[1]


@pytest.mark.django_db(transaction=True)
def test_run_fails_when_the_dump_role_cannot_bypass_rls(
    settings: Settings,
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    settings.DATABASE_BACKUP = django_settings.DATABASE_OWNER
    run = queued_run()

    services.perform_run(run.id)

    assert fake_restic.calls == [restic.unlock_argv()]
    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "backup_role_missing"


@pytest.mark.django_db(transaction=True)
def test_unconfigured_destination_fails_the_run_with_the_sentence(
    fake_restic: FakeBinary,
) -> None:
    run = queued_run()

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "not_configured"
    assert run.reason == "Er is nog geen back-up ingesteld."
    assert fake_restic.calls == []


@pytest.mark.django_db(transaction=True)
def test_missing_backup_password_fails_the_run(
    settings: Settings, configured_folder: Path, fake_restic: FakeBinary
) -> None:
    settings.BRIEVIE_BACKUP_PASSWORD = ""
    run = queued_run()

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.reason_code == "no_backup_password"
    assert fake_restic.calls == []


def test_closed_run_makes_the_job_a_no_op(fake_restic: FakeBinary) -> None:
    run = BackupRun.objects.create(kind="backup", status=BackupRun.Status.INTERRUPTED)

    services.perform_run(run.id)

    assert fake_restic.calls == []
    run.refresh_from_db()
    assert run.status == BackupRun.Status.INTERRUPTED
    assert run.started_at is None


def test_missing_run_makes_the_job_a_no_op(fake_restic: FakeBinary) -> None:
    services.perform_run(UUID(int=7))
    assert fake_restic.calls == []


@pytest.mark.django_db(transaction=True)
def test_a_running_run_is_resumed_by_a_retried_job(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = BackupRun.objects.create(kind="backup", status=BackupRun.Status.RUNNING)
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_lock_taken_fails_the_run_as_busy(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = queued_run()
    with psycopg.connect(_owner_conninfo(), autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", [BACKUP_LOCK_KEY])
        services.perform_run(run.id)

    assert fake_restic.calls == []
    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "busy"


@pytest.mark.django_db(transaction=True)
def test_unexpected_error_is_internal_and_logs_the_type_only(
    configured_folder: Path,
    caplog: pytest.LogCaptureFixture,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(**kwargs: object) -> None:
        raise RuntimeError("secret-detail-in-message")

    monkeypatch.setattr(services, "build_manifest", explode)
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=summary(DB_SIZE))

    with caplog.at_level(logging.DEBUG):
        services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "internal_error"
    assert "RuntimeError" in caplog.text
    assert "secret-detail-in-message" not in caplog.text


# --- wait_for_run --------------------------------------------------------------------------


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []
        self.on_sleep: Callable[[int], None] = lambda count: None

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        self.on_sleep(len(self.slept))


def test_wait_returns_the_finished_run() -> None:
    run = queued_run()
    time = FakeTime()

    def finish_on_second_poll(count: int) -> None:
        if count == 1:
            BackupRun.objects.filter(id=run.id).update(status=BackupRun.Status.RUNNING)
        if count == 2:
            BackupRun.objects.filter(id=run.id).update(status=BackupRun.Status.SUCCEEDED)

    time.on_sleep = finish_on_second_poll

    done = services.wait_for_run(run.id, timeout=60, sleep=time.sleep, clock=time.clock)

    assert done.status == BackupRun.Status.SUCCEEDED
    assert len(time.slept) == 2


def test_wait_gives_up_when_nobody_picks_up_the_run() -> None:
    run = queued_run()
    time = FakeTime()

    with pytest.raises(TimeoutError):
        services.wait_for_run(
            run.id, timeout=600, pickup_timeout=10, poll=3, sleep=time.sleep, clock=time.clock
        )

    assert time.now == 10  # never sleeps beyond the pick-up time
    run.refresh_from_db()
    assert run.status == BackupRun.Status.QUEUED


def test_wait_gives_up_after_the_timeout_on_a_running_run() -> None:
    run = BackupRun.objects.create(kind="backup", status=BackupRun.Status.RUNNING)
    time = FakeTime()

    with pytest.raises(TimeoutError):
        services.wait_for_run(
            run.id, timeout=20, pickup_timeout=5, poll=3, sleep=time.sleep, clock=time.clock
        )

    assert time.now == 20


# --- the backup command --------------------------------------------------------------------


def _finished(status: str, **fields: object) -> Callable[..., BackupRun]:
    def wait(run_id: UUID, **kwargs: object) -> BackupRun:
        BackupRun.objects.filter(id=run_id).update(status=status, **fields)
        return BackupRun.objects.get(id=run_id)

    return wait


def test_backup_command_exit_codes(
    configured_folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        services, "wait_for_run", _finished(BackupRun.Status.SUCCEEDED, size_bytes=1_500_000)
    )
    call_command("backup")
    out = capsys.readouterr().out
    assert "Back-up gestart" in out
    assert "De back-up is gelukt" in out
    assert "1,4\xa0MB" in out

    monkeypatch.setattr(
        services,
        "wait_for_run",
        _finished(
            BackupRun.Status.FAILED, reason_code="busy", reason="Er is al een back-up bezig."
        ),
    )
    BackupRun.objects.update(status=BackupRun.Status.SUCCEEDED)
    with pytest.raises(CommandError) as failed:
        call_command("backup")
    assert failed.value.returncode == 1
    assert str(failed.value) == "Er is al een back-up bezig."

    def time_out(run_id: UUID, **kwargs: object) -> BackupRun:
        raise TimeoutError

    monkeypatch.setattr(services, "wait_for_run", time_out)
    BackupRun.objects.update(status=BackupRun.Status.FAILED)
    with pytest.raises(CommandError) as not_picked_up:
        call_command("backup")
    assert not_picked_up.value.returncode == 2
    assert "niet opgepakt" in str(not_picked_up.value)

    BackupRun.objects.update(status=BackupRun.Status.RUNNING)
    with pytest.raises(CommandError) as still_running:
        call_command("backup")
    assert still_running.value.returncode == 2
    assert "nog bezig" in str(still_running.value)


def test_backup_command_check_requests_a_check(
    configured_folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, object] = {}

    def wait(run_id: UUID, **kwargs: object) -> BackupRun:
        seen.update(kwargs)
        return _finished(BackupRun.Status.SUCCEEDED)(run_id)

    monkeypatch.setattr(services, "wait_for_run", wait)
    call_command("backup", "--check", "--timeout", "30")

    assert BackupRun.objects.get().kind == "check"
    assert seen["timeout"] == 30
    assert "De controle is gelukt." in capsys.readouterr().out


# --- status --------------------------------------------------------------------------------


def _version_tool(directory: Path, name: str, output: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\necho '{output}'\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_status_lists_versions_and_last_runs(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tools = tmp_path / "tools"
    tools.mkdir()
    _version_tool(tools, "pg_dump", "pg_dump (PostgreSQL) 18.1 (Debian 18.1-1)")
    _version_tool(tools, "restic", "restic 0.17.3 compiled with go1.23.1 on linux/amd64")
    monkeypatch.setenv("PATH", f"{tools}:/usr/bin:/bin")
    settings.BRIEVIE_RESTIC_BINARY = "restic"
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://nas:8000/b"
    settings.BRIEVIE_BACKUP_CONFIG_DIR = str(tmp_path / "config-dir")
    BackupRun.objects.create(kind="backup", status=BackupRun.Status.SUCCEEDED, size_bytes=2048)
    BackupRun.objects.create(kind="check", status=BackupRun.Status.SUCCEEDED)
    BackupRun.objects.create(
        kind="backup",
        status=BackupRun.Status.FAILED,
        reason_code="destination_full",
        reason="Er is geen ruimte meer op de bestemming van de back-up.",
    )

    status = services.status_summary()

    assert status.pg_dump_version == "18.1"
    assert status.restic_version == "0.17.3"
    assert status.postgres_version.split(".")[0] == "18"
    assert status.brievie_version
    assert status.destination_kind == "rest"
    assert status.last_success is not None
    assert status.last_success.size_bytes == 2048
    assert status.last_check is not None
    assert status.last_failure is not None
    assert status.open_run is None

    call_command("backup_status")
    out = capsys.readouterr().out
    assert "0.17.3" in out
    assert "18.1" in out
    assert "Er is geen ruimte meer" in out
    assert "nas" not in out
    assert "rest:" not in out
    assert str(tmp_path) not in out
    assert "/data" not in out


def test_status_shows_a_question_mark_for_a_missing_binary(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    settings.BRIEVIE_RESTIC_BINARY = "restic"

    status = services.status_summary()

    assert status.pg_dump_version == "?"
    assert status.restic_version == "?"
    assert status.destination_kind == ""
    assert status.last_success is None


def test_status_command_without_runs(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    queued_run()

    call_command("backup_status")

    out = capsys.readouterr().out
    assert "nog nooit" in out
    assert "?" in out


def test_status_command_shows_a_bad_destination_setting_without_its_value(
    settings: Settings, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://user:pw@nas:8000/b"
    with mock.patch.object(services, "_tool_version", return_value="?"):
        call_command("backup_status")
    out = capsys.readouterr().out
    assert "pw@" not in out
    assert "nas" not in out


# --- abandoned runs (review round 1) -------------------------------------------------------


def _job_of(run: BackupRun) -> ProcrastinateJob:
    job: ProcrastinateJob = ProcrastinateJob.objects.get(
        task_name="run_backup", args__run_id=str(run.id)
    )
    return job


def test_open_run_whose_job_failed_is_closed_and_replaced(configured_folder: Path) -> None:
    old = services.request_run("backup")
    ProcrastinateJob.objects.filter(id=_job_of(old).id).update(status="failed")

    new = services.request_run("backup")

    assert new.id != old.id
    assert new.status == BackupRun.Status.QUEUED
    assert _job_of(new).status == "todo"
    old.refresh_from_db()
    assert old.status == BackupRun.Status.INTERRUPTED
    assert old.reason_code == "abandoned"
    assert old.reason == "De vorige back-up is afgebroken. Er is een nieuwe gestart."
    # The new run's job removes what the abandoned run may have left.
    assert _job_of(new).args == {"run_id": str(new.id), "clean_up_run_ids": [str(old.id)]}
    assert old.finished_at is not None


def test_open_run_without_any_job_is_closed_and_replaced(configured_folder: Path) -> None:
    old = BackupRun.objects.create(kind="backup", status=BackupRun.Status.RUNNING)

    new = services.request_run("backup")

    assert new.id != old.id
    old.refresh_from_db()
    assert old.status == BackupRun.Status.INTERRUPTED


def test_open_run_with_a_live_job_is_returned(configured_folder: Path) -> None:
    old = services.request_run("backup")
    ProcrastinateJob.objects.filter(id=_job_of(old).id).update(status="doing")

    again = services.request_run("backup")

    assert again.id == old.id
    assert ProcrastinateJob.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_mark_running_failing_never_leaves_a_blocking_run(
    configured_folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = services.request_run("backup")

    def broken(*args: object, **kwargs: object) -> None:
        raise DatabaseError("database went away")

    monkeypatch.setattr(services, "_mark_running", broken)
    monkeypatch.setattr(services, "_finish", broken)

    services.perform_run(run.id)  # never raises

    # The job ended (perform_run returned); the run is still open but has no live job.
    ProcrastinateJob.objects.filter(id=_job_of(run).id).update(status="succeeded")
    monkeypatch.undo()
    new = services.request_run("backup")
    assert new.id != run.id
    run.refresh_from_db()
    assert run.status == BackupRun.Status.INTERRUPTED
    assert fake_restic.calls == []


@pytest.mark.django_db(transaction=True)
def test_failing_final_record_logs_the_type_only(
    configured_folder: Path,
    caplog: pytest.LogCaptureFixture,
    fake_restic: FakeBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = queued_run()

    def broken(*args: object, **kwargs: object) -> None:
        raise DatabaseError("secret-in-db-message")

    monkeypatch.setattr(services, "_finish", broken)
    with caplog.at_level(logging.DEBUG):
        services.perform_run(run.id)

    assert "DatabaseError" in caplog.text
    assert "secret-in-db-message" not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_resumed_run_keeps_its_started_at(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    started = timezone.now() - timedelta(hours=1)
    run = BackupRun.objects.create(
        kind="backup", status=BackupRun.Status.RUNNING, started_at=started
    )
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.started_at == started
    assert run.status == BackupRun.Status.SUCCEEDED


# --- orphan database snapshots (ruling 3) --------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_failed_files_step_removes_the_runs_database_snapshot(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=summary(DB_SIZE))
    fake_restic.respond(files_prefix(run.id), stderr="Fatal: unable to save", code=1)

    services.perform_run(run.id)

    assert without_snapshot(fake_restic.calls) == [
        restic.unlock_argv(),
        dump_call(run.id),
        files_call(run.id),
        restic.forget_run_argv(run.id),
    ]
    run.refresh_from_db()
    assert run.reason_code == "restic_failed"


@pytest.mark.django_db(transaction=True)
def test_failure_after_the_dump_of_any_kind_removes_the_snapshot(
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("manifest")

    # Writing the manifest is the first step after the dump.
    monkeypatch.setattr(services, "write_private", explode)
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=summary(DB_SIZE))

    services.perform_run(run.id)

    assert fake_restic.calls[-1] == restic.forget_run_argv(run.id)
    run.refresh_from_db()
    assert run.reason_code == "internal_error"


@pytest.mark.django_db(transaction=True)
def test_failed_dump_needs_no_clean_up(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), code=1)

    services.perform_run(run.id)

    assert restic.forget_run_argv(run.id) not in fake_restic.calls


@pytest.mark.django_db(transaction=True)
def test_append_only_never_removes_the_orphan_snapshot(
    settings: Settings,
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    settings.BRIEVIE_BACKUP_APPEND_ONLY = True
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=summary(DB_SIZE))
    fake_restic.respond(files_prefix(run.id), code=1)

    services.perform_run(run.id)

    assert all(call[0] != "forget" for call in fake_restic.calls)


@pytest.mark.django_db(transaction=True)
def test_failed_clean_up_keeps_the_runs_reason(
    configured_folder: Path,
    caplog: pytest.LogCaptureFixture,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stdout=summary(DB_SIZE))
    fake_restic.respond(files_prefix(run.id), code=1)
    fake_restic.respond(tuple(restic.forget_run_argv(run.id)), code=11)

    with caplog.at_level(logging.DEBUG):
        services.perform_run(run.id)

    run.refresh_from_db()
    assert run.reason_code == "restic_failed"
    assert "repository_locked" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_resumed_run_removes_its_earlier_snapshots_before_the_new_dump(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = BackupRun.objects.create(
        kind="backup", status=BackupRun.Status.RUNNING, started_at=timezone.now()
    )
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    assert without_snapshot(fake_restic.calls)[:3] == [
        restic.unlock_argv(),
        restic.forget_run_argv(run.id),
        dump_call(run.id),
    ]


@pytest.mark.django_db(transaction=True)
def test_append_only_resumed_run_removes_nothing(
    settings: Settings,
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    settings.BRIEVIE_BACKUP_APPEND_ONLY = True
    run = BackupRun.objects.create(kind="backup", status=BackupRun.Status.RUNNING)
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    assert all(call[0] != "forget" for call in fake_restic.calls)


# --- timeouts (ruling 4) -------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_only_unlock_and_clean_up_have_a_timeout(
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, float | None]] = []
    real_run = restic.run

    def recording_run(
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        secrets: Sequence[str],
        timeout: float | None = None,
    ) -> restic.ResticResult:
        seen.append((argv[0] if argv[0] != "backup" else argv[-1].split("=")[0], timeout))
        return real_run(argv, env, secrets=secrets, timeout=timeout)

    monkeypatch.setattr(restic, "run", recording_run)
    run = BackupRun.objects.create(kind="backup", status=BackupRun.Status.RUNNING)
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    dump_last_arg = "--snapshot"
    files_last_arg = services.file_paths()[-1]
    assert seen == [
        ("unlock", services.SHORT_RESTIC_TIMEOUT),
        ("forget", services.SHORT_RESTIC_TIMEOUT),  # the resumed run's clean-up
        (dump_last_arg, None),
        (files_last_arg, None),
        ("snapshots", services.SHORT_RESTIC_TIMEOUT),  # retention: the clock, half runs
        ("forget", None),  # safety snapshots older than 30 days
        ("forget", None),  # retention with --prune
    ]
    assert services.SHORT_RESTIC_TIMEOUT == 600


@pytest.mark.django_db(transaction=True)
def test_check_has_no_timeout(
    configured_folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[float | None] = []
    real_run = restic.run

    def recording_run(
        argv: Sequence[str],
        env: Mapping[str, str],
        *,
        secrets: Sequence[str],
        timeout: float | None = None,
    ) -> restic.ResticResult:
        if argv[0] == "check":
            seen.append(timeout)
        return real_run(argv, env, secrets=secrets, timeout=timeout)

    monkeypatch.setattr(restic, "run", recording_run)
    services.perform_run(queued_run("check").id)

    assert seen == [None]


@pytest.mark.django_db(transaction=True)
def test_unlock_timeout_fails_the_run(
    configured_folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hanging(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="restic", timeout=600)

    monkeypatch.setattr(subprocess, "run", hanging)
    run = queued_run()

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "restic_failed"


# --- follow-up: abandoned runs' snapshots, interrupted runs in the status ------------------


def test_new_job_carries_the_clean_up_of_an_earlier_abandoned_run(
    configured_folder: Path,
) -> None:
    first = services.request_run("backup")
    ProcrastinateJob.objects.filter(id=_job_of(first).id).update(status="failed")
    second = services.request_run("backup")
    ProcrastinateJob.objects.filter(id=_job_of(second).id).update(status="failed")

    third = services.request_run("backup")

    assert sorted(_job_of(third).args["clean_up_run_ids"]) == sorted(
        [str(first.id), str(second.id)]
    )


def test_a_fresh_request_has_no_clean_up(configured_folder: Path) -> None:
    run = services.request_run("backup")
    assert _job_of(run).args == {"run_id": str(run.id)}


def test_an_abandoned_check_needs_no_clean_up(configured_folder: Path) -> None:
    old = services.request_run("check")
    ProcrastinateJob.objects.filter(id=_job_of(old).id).update(status="failed")

    new = services.request_run("check")

    assert _job_of(new).args == {"run_id": str(new.id)}


@pytest.mark.django_db(transaction=True)
def test_run_removes_abandoned_snapshots_after_unlock(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    abandoned = BackupRun.objects.create(kind="backup", status=BackupRun.Status.INTERRUPTED)
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id, clean_up=[abandoned.id])

    assert without_snapshot(fake_restic.calls)[:3] == [
        restic.unlock_argv(),
        restic.forget_run_argv(abandoned.id),
        dump_call(run.id),
    ]
    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_failed_abandoned_clean_up_is_logged_and_the_run_goes_on(
    configured_folder: Path,
    caplog: pytest.LogCaptureFixture,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    abandoned_id = UUID(int=42)
    run = queued_run()
    answer_both_backups(fake_restic, run.id)
    fake_restic.respond(tuple(restic.forget_run_argv(abandoned_id)), code=11)

    with caplog.at_level(logging.DEBUG):
        services.perform_run(run.id, clean_up=[abandoned_id])

    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED
    assert "repository_locked" in caplog.text


@pytest.mark.django_db(transaction=True)
def test_append_only_never_removes_abandoned_snapshots(
    settings: Settings,
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    settings.BRIEVIE_BACKUP_APPEND_ONLY = True
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id, clean_up=[UUID(int=42)])

    assert all(call[0] != "forget" for call in fake_restic.calls)


def test_status_shows_an_interrupted_run_newer_than_the_last_success(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://nas:8000/b"
    BackupRun.objects.create(
        kind="backup",
        status=BackupRun.Status.INTERRUPTED,
        reason_code="interrupted_by_restore",
        reason="Afgebroken door herstel.",
    )
    BackupRun.objects.create(kind="backup", status=BackupRun.Status.SUCCEEDED, size_bytes=10)

    assert services.status_summary().last_interrupted is None

    newer = BackupRun.objects.create(
        kind="backup",
        status=BackupRun.Status.INTERRUPTED,
        reason_code="abandoned",
        reason="De vorige back-up is afgebroken. Er is een nieuwe gestart.",
    )
    status = services.status_summary()
    assert status.last_interrupted is not None
    assert status.last_interrupted.id == newer.id

    call_command("backup_status")
    out = capsys.readouterr().out
    assert "Laatst afgebroken:" in out
    assert "De vorige back-up is afgebroken." in out
    assert "nas" not in out
    assert str(tmp_path) not in out


def test_status_shows_an_interrupted_run_when_nothing_succeeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    run = BackupRun.objects.create(kind="backup", status=BackupRun.Status.INTERRUPTED)
    status = services.status_summary()
    assert status.last_interrupted is not None
    assert status.last_interrupted.id == run.id


# --- the dump and the manifest read one snapshot ---------------------------------------


class SnapshotSpy:
    """The counting connection and the exported snapshot id, with the order of events."""

    def __init__(self) -> None:
        self.connections: list[psycopg.Connection[tuple[object, ...]]] = []
        self.snapshot_ids: list[str] = []
        self.events: list[str] = []


@pytest.fixture
def snapshot_spy(monkeypatch: pytest.MonkeyPatch) -> SnapshotSpy:
    from apps.backup import manifest

    spy = SnapshotSpy()
    real_counting = manifest.counting_transaction
    real_export = manifest.export_snapshot
    real_build = manifest.build_manifest
    real_run = restic.run

    @contextmanager
    def counting(conninfo: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
        with real_counting(conninfo) as conn:
            spy.connections.append(conn)
            yield conn

    def export(conn: psycopg.Connection[tuple[object, ...]]) -> str:
        snapshot_id = real_export(conn)
        spy.snapshot_ids.append(snapshot_id)
        spy.events.append("exported")
        return snapshot_id

    def build(**kwargs: object) -> object:
        result = real_build(**kwargs)  # type: ignore[arg-type]
        spy.events.append("counted")
        return result

    def run(argv: Sequence[str], env: Mapping[str, str], **kwargs: object) -> object:
        if "--stdin-from-command" in argv:
            open_now = bool(spy.connections) and not spy.connections[-1].closed
            spy.events.append(f"dump (counting connection open: {open_now})")
        return real_run(argv, env, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(services, "counting_transaction", counting)
    monkeypatch.setattr(services, "export_snapshot", export)
    monkeypatch.setattr(services, "build_manifest", build)
    monkeypatch.setattr(restic, "run", run)
    return spy


@pytest.mark.django_db(transaction=True)
def test_the_dump_reads_the_snapshot_the_manifest_counted(
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
    snapshot_spy: SnapshotSpy,
) -> None:
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    (snapshot_id,) = snapshot_spy.snapshot_ids
    assert snapshot_id  # e.g. 00000003-0000001B-1
    dump = dump_call(run.id, f"--snapshot={snapshot_id}")
    assert dump in fake_restic.calls
    assert snapshot_spy.events == [
        "exported",
        "counted",
        "dump (counting connection open: True)",
    ]
    (conn,) = snapshot_spy.connections
    assert conn.closed
    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_the_counting_connection_is_closed_when_the_dump_fails(
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
    snapshot_spy: SnapshotSpy,
) -> None:
    run = queued_run()
    fake_restic.respond(db_prefix(run.id), stderr="pg_dump: error: snapshot", code=1)

    services.perform_run(run.id)

    (conn,) = snapshot_spy.connections
    assert conn.closed
    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED


@pytest.mark.django_db(transaction=True)
def test_a_letter_uploaded_during_the_dump_is_not_counted(
    ws_a: Workspace,
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_backup_manifest import upload_letter

    real_run = restic.run

    def run_and_upload(argv: Sequence[str], env: Mapping[str, str], **kwargs: object) -> object:
        if "--stdin-from-command" in argv:
            upload_letter(ws_a, 1)  # committed while the dump runs
        return real_run(argv, env, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(restic, "run", run_and_upload)
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    manifest_path = Path(django_settings.BRIEVIE_BACKUP_CONFIG_DIR) / MANIFEST_NAME
    manifest = manifest_from_json(manifest_path.read_text(encoding="utf-8"))
    assert manifest.workspaces[ws_a.id].documents == 0


@pytest.mark.django_db(transaction=True)
def test_the_counting_connection_is_not_djangos(
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No Django transaction is open while restic runs the dump.
    real_run = restic.run
    seen: list[bool] = []

    def run(argv: Sequence[str], env: Mapping[str, str], **kwargs: object) -> object:
        if "--stdin-from-command" in argv:
            seen.append(connection.in_atomic_block)
        return real_run(argv, env, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(restic, "run", run)
    run_ = queued_run()
    answer_both_backups(fake_restic, run_.id)
    services.perform_run(run_.id)
    assert seen == [False]


def test_backup_command_names_its_code_for_the_wrapper(
    configured_folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        services,
        "wait_for_run",
        _finished(
            BackupRun.Status.FAILED,
            reason_code="destination_not_ready",
            reason="De bestemming voor de back-up is nog niet klaar voor gebruik.",
        ),
    )
    with pytest.raises(CommandError):
        call_command("backup")
    assert "code=" not in capsys.readouterr().err  # only for ./brievie

    monkeypatch.setenv("BRIEVIE_WRAPPER", "1")
    BackupRun.objects.update(status=BackupRun.Status.FAILED)
    with pytest.raises(CommandError):
        call_command("backup")
    assert capsys.readouterr().err.strip() == "code=destination_not_ready"

    def time_out(run_id: UUID, **kwargs: object) -> BackupRun:
        raise TimeoutError

    monkeypatch.setattr(services, "wait_for_run", time_out)
    BackupRun.objects.update(status=BackupRun.Status.FAILED)
    with pytest.raises(CommandError):
        call_command("backup")
    assert capsys.readouterr().err.strip() == "code=not_picked_up"


# --- status --list: the runs on the destination --------------------------------------------


def _snapshot(snapshot_id: str, when: str, run_id: UUID, kind: str) -> dict[str, object]:
    return {
        "id": snapshot_id,
        "time": when,
        "tags": [f"run:{run_id}", f"kind:{kind}"],
        "paths": ["/x"],
    }


@pytest.mark.django_db(transaction=True)
def test_status_list_shows_every_run_on_the_destination(
    configured_folder: Path, fake_restic: FakeBinary, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = uuid4(), uuid4()
    db = [
        _snapshot("d0000001", "2026-11-10T03:00:00Z", old, "db"),
        _snapshot("d0000002", "2026-11-17T03:00:00Z", new, "db"),
    ]
    files = [_snapshot("f0000001", "2026-11-10T03:05:00Z", old, "files")]
    fake_restic.respond(
        ("snapshots", "--no-lock", "--json", "--tag", "kind:db"), stdout=json.dumps(db).encode()
    )
    fake_restic.respond(
        ("snapshots", "--no-lock", "--json", "--tag", "kind:files"),
        stdout=json.dumps(files).encode(),
    )

    call_command("backup_status", "--list")

    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == f"{new}  17-11-2026 04:00  compleet: nee"
    assert lines[1] == f"{old}  10-11-2026 04:00  compleet: ja"
    assert len(lines) == 2
    assert all("/" not in line.split("  ")[0] for line in lines)  # no paths


@pytest.mark.django_db(transaction=True)
def test_status_list_says_when_there_are_no_runs(
    configured_folder: Path, fake_restic: FakeBinary, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_restic.respond(("snapshots",), stdout=b"[]")
    call_command("backup_status", "--list")
    assert capsys.readouterr().out.strip() == "Er staan nog geen back-ups op de bestemming."


@pytest.mark.parametrize(
    ("status", "code"), [("queued", "not_picked_up"), ("running", "still_running")]
)
def test_backup_command_tells_the_wrapper_why_it_waited_in_vain(
    configured_folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    code: str,
) -> None:
    def time_out(run_id: UUID, **kwargs: object) -> BackupRun:
        BackupRun.objects.filter(id=run_id).update(status=status)
        raise TimeoutError

    monkeypatch.setattr(services, "wait_for_run", time_out)
    monkeypatch.setenv("BRIEVIE_WRAPPER", "1")
    with pytest.raises(CommandError) as caught:
        call_command("backup")
    assert caught.value.returncode == 2
    assert capsys.readouterr().err.strip() == f"code={code}"


# --- what a back-up is: one time, one host, the safety tag, its retention -----------------


def snapshot_json(run_id: UUID, kind: str, when: object) -> dict[str, object]:
    return {
        "id": uuid4().hex,
        "time": str(when).replace(" ", "T"),
        "tags": [f"run:{run_id}", f"kind:{kind}"],
        "paths": ["/x"],
    }


@pytest.mark.django_db(transaction=True)
def test_both_halves_of_a_run_carry_the_runs_start_and_the_installations_host(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary, env_copy: Path
) -> None:
    (env_copy.parent / "installation-id").write_text("0123456789abcdef0123456789abcdef\n")
    host = "brievie-0123456789ab"
    run = queued_run()
    fake_restic.respond(
        ("backup", "--json", "--host", host, *restic.tags(run.id, "db")), stdout=summary(DB_SIZE)
    )
    fake_restic.respond(
        ("backup", "--json", "--host", host, *restic.tags(run.id, "files")),
        stdout=summary(FILES_SIZE),
    )
    fake_restic.respond(("snapshots", "--json", "--host"), stdout=b"[]")

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED
    backups = [call for call in fake_restic.calls if call[0] == "backup"]
    times = {call[call.index("--time") + 1] for call in backups}
    assert run.started_at is not None
    assert times == {run.started_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")}
    assert all(call[call.index("--host") + 1] == host for call in backups)
    assert fake_restic.calls[-3:] == retention(host)


@pytest.mark.parametrize("content", ["not-hex", "0123", "", "0123456789ABCDEF0123456789ABCDEF"])
@pytest.mark.django_db(transaction=True)
def test_an_invalid_installation_id_means_the_shared_host(
    configured_folder: Path, env_copy: Path, content: str
) -> None:
    (env_copy.parent / "installation-id").write_text(content)
    assert services.backup_host() == restic.HOST


@pytest.mark.django_db(transaction=True)
def test_a_linked_installation_id_is_never_read(
    configured_folder: Path, env_copy: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere-id"
    elsewhere.write_text("0123456789abcdef0123456789abcdef")
    (env_copy.parent / "installation-id").symlink_to(elsewhere)
    assert services.backup_host() == restic.HOST


@pytest.mark.django_db(transaction=True)
def test_the_installation_id_is_not_part_of_a_back_up(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    config = django_settings.BRIEVIE_BACKUP_CONFIG_DIR
    assert services.file_excludes() == [
        f"{config}/installation-id",
        f"{config}/installation-id.tmp",
    ]
    run = queued_run()
    answer_both_backups(fake_restic, run.id)
    services.perform_run(run.id)
    files = next(call for call in fake_restic.calls if "kind:files" in call)
    assert f"{config}/installation-id" in files


@pytest.mark.django_db(transaction=True)
def test_a_safety_backup_is_tagged_and_applies_no_retention(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = services.request_run("backup", requested_by="restore")
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    backups = [call for call in fake_restic.calls if call[0] == "backup"]
    assert len(backups) == 2
    assert all(restic.SAFETY_TAG in call for call in backups)
    assert all(call[0] not in ("forget", "snapshots") for call in fake_restic.calls)


@pytest.mark.django_db(transaction=True)
def test_a_snapshot_from_the_future_stops_retention(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = queued_run()
    answer_both_backups(fake_restic, run.id)
    future = timezone.now() + timedelta(days=3)
    listed = [snapshot_json(uuid4(), "db", future.astimezone(UTC).isoformat())]
    fake_restic.respond(("snapshots", "--json", "--host"), stdout=json.dumps(listed).encode())

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED
    assert run.reason_code == "clock_skew"
    assert run.reason == (
        "De klok van de server of van een back-up klopt niet; er wordt niets opgeruimd."
    )
    assert all(call[0] != "forget" for call in fake_restic.calls)


@pytest.mark.django_db(transaction=True)
def test_status_shows_the_warning_of_the_last_backup(
    configured_folder: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "_tool_version", lambda *_args: "?")
    BackupRun.objects.create(
        kind="backup",
        status=BackupRun.Status.SUCCEEDED,
        finished_at=timezone.now(),
        reason_code="clock_skew",
        reason="De klok van de server of van een back-up klopt niet; er wordt niets opgeruimd.",
    )
    call_command("backup_status")
    assert (
        "Let op: De klok van de server of van een back-up klopt niet; er wordt niets opgeruimd."
        in capsys.readouterr().out
    )


@pytest.mark.django_db(transaction=True)
def test_half_runs_older_than_two_days_go_before_retention(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = queued_run()
    answer_both_backups(fake_restic, run.id)
    now = timezone.now()
    old_half, young_half, whole = uuid4(), uuid4(), uuid4()
    listed = [
        snapshot_json(old_half, "db", (now - timedelta(days=3)).astimezone(UTC).isoformat()),
        snapshot_json(young_half, "db", (now - timedelta(days=1)).astimezone(UTC).isoformat()),
        snapshot_json(whole, "db", (now - timedelta(days=5)).astimezone(UTC).isoformat()),
        snapshot_json(whole, "files", (now - timedelta(days=5)).astimezone(UTC).isoformat()),
    ]
    fake_restic.respond(("snapshots", "--json", "--host"), stdout=json.dumps(listed).encode())

    services.perform_run(run.id)

    assert fake_restic.calls[-4:] == [
        restic.snapshots_host_argv(restic.HOST),
        restic.forget_run_argv(old_half),
        restic.forget_safety_argv(),
        restic.forget_argv(),
    ]


@pytest.mark.django_db(transaction=True)
def test_a_backup_without_the_env_copy_fails_and_makes_nothing(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary, env_copy: Path
) -> None:
    env_copy.unlink()
    run = queued_run()
    answer_both_backups(fake_restic, run.id)

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "env_copy_missing"
    assert "./brievie start" in run.reason
    assert all(call[0] != "backup" for call in fake_restic.calls)


def test_backup_command_safety_says_its_run_id_to_the_wrapper(
    configured_folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(services, "wait_for_run", _finished(BackupRun.Status.SUCCEEDED))
    monkeypatch.setenv("BRIEVIE_WRAPPER", "1")
    for option in ("--safety", "--no-prune"):
        BackupRun.objects.all().delete()
        call_command("backup", option)
        run = BackupRun.objects.get()
        assert run.requested_by == "restore"
        assert f"safety_run_id={run.id}" in capsys.readouterr().out.splitlines()
    monkeypatch.delenv("BRIEVIE_WRAPPER")
    BackupRun.objects.all().delete()
    call_command("backup", "--safety")
    assert "safety_run_id=" not in capsys.readouterr().out

"""The emergency back-up of an update: a label on both snapshots, the local fallback
repository in the volume `update-backup`, and finding a back-up by its label.

With a destination the back-up goes through the keeper (`backup --safety --label`);
without one `update_backup` makes it in the one-off admin container, into the fallback
(mounted at /backup for that call only). Folders only under tmp_path; a fake restic.
"""

import io
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone
from pytest_django import Settings

from apps.backup import destination, restic, restore, services
from apps.backup.errors import REASONS, BackupError
from apps.backup.models import BackupRun
from tests.backup_support import FakeBinary

LABEL = "pre-update-0.3.0"
PASSWORD = "p" * 40
DB_PASSWORD = "secret-db-password-of-the-backup-role"


def summary(total: int) -> bytes:
    return json.dumps({"message_type": "summary", "total_bytes_processed": total}).encode()


@pytest.fixture
def fake_pg_dump(fake_tool: Callable[[str], FakeBinary]) -> FakeBinary:
    pg_dump = fake_tool("pg_dump")
    pg_dump.respond(("--version",), stdout=b"pg_dump (PostgreSQL) 18.1\n")
    return pg_dump


@pytest.fixture(autouse=True)
def env_copy(settings: Settings) -> Path:
    config = Path(settings.BRIEVIE_BACKUP_CONFIG_DIR)
    config.mkdir(parents=True, exist_ok=True)
    (config / "env").write_text("BRIEVIE_HOSTNAME=brievie.local\n")
    return config / "env"


@pytest.fixture
def fallback_folder(tmp_path: Path, settings: Settings) -> Path:
    """The volume update-backup, as the admin container sees it: a folder at /backup with
    the fallback marker (written by `backup_init --update-fallback`)."""
    mount = tmp_path / "update-backup"
    (mount / destination.REPOSITORY_DIR).mkdir(parents=True)
    marker = {
        "format": destination.MARKER_FORMAT,
        "repository": destination.REPOSITORY_DIR,
        "purpose": destination.UPDATE_FALLBACK_PURPOSE,
    }
    (mount / destination.MARKER_NAME).write_text(json.dumps(marker))
    settings.BRIEVIE_BACKUP_DIR_SET = True
    settings.BRIEVIE_BACKUP_MOUNT = str(mount)
    settings.BRIEVIE_BACKUP_REPOSITORY = ""
    settings.BRIEVIE_BACKUP_PASSWORD = PASSWORD
    return mount


def answer_backups(fake_restic: FakeBinary) -> None:
    # Every `backup` call answers with a summary; the prefix is the same for both halves.
    fake_restic.respond(("backup", "--json"), stdout=summary(1000))


# --- pure: tags, labels, the fallback's retention ------------------------------------------


def test_tags_carry_the_label() -> None:
    run_id = uuid4()
    db = restic.backup_stdin_argv(run_id, ["pg_dump"], safety=True, label=LABEL)
    files = restic.backup_files_argv(run_id, ["/data/documents"], safety=True, label=LABEL)
    for argv in (db, files):
        assert argv[argv.index(f"label:{LABEL}") - 1] == "--tag"
        assert restic.SAFETY_TAG in argv
    assert not any(a.startswith("label:") for a in restic.backup_stdin_argv(run_id, ["x"]))


@pytest.mark.parametrize(
    "label",
    [
        "",
        "pre-update-",
        "pre-update-latest",
        "pre-update-0.3",
        "x pre-update-0.3.0",
        "pre-update-0.3.0 ",
        "pre-update-01.3.0",
        "pre-update-0.3.0\n",
    ],
)
def test_bad_labels_are_refused(label: str) -> None:
    with pytest.raises(BackupError) as caught:
        restic.check_label(label)
    assert caught.value.code == "label_invalid"


def test_good_label_passes() -> None:
    restic.check_label(LABEL)
    restic.check_label("pre-update-10.20.30")


def test_fallback_forget_keeps_only_the_newest_run() -> None:
    argv = restic.fallback_forget_argv("brievie-0123456789ab")
    assert argv == [
        "forget",
        "--prune",
        "--host",
        "brievie-0123456789ab",
        "--group-by",
        "host,paths",
        "--keep-last",
        "1",
    ]
    assert "--unsafe-allow-remove-all" not in argv


def test_the_reasons_have_dutch_sentences() -> None:
    assert "label_invalid" in REASONS
    assert "noodback-up" in str(REASONS["update_backup_unavailable"])


# --- which repository a run writes to --------------------------------------------------------


def test_a_fallback_run_refuses_a_real_destination(configured_folder: Path) -> None:
    # The fallback prunes down to one run: it must never write to the user's destination.
    with pytest.raises(BackupError) as caught:
        destination.backup_repository(fallback=True)
    assert caught.value.code == "update_backup_unavailable"


def test_an_ordinary_run_refuses_the_fallback(fallback_folder: Path) -> None:
    # The keeper's retention must never reach the way back of an update.
    with pytest.raises(BackupError) as caught:
        destination.backup_repository(fallback=False)
    assert caught.value.code == "destination_not_ready"


def test_the_fallback_is_read_like_a_folder(fallback_folder: Path) -> None:
    # A restore (configured_repository) opens it exactly like a folder destination.
    repository = destination.configured_repository()
    assert repository.kind == "folder"
    assert destination.backup_repository(fallback=True) == repository


def test_initialise_writes_the_fallback_marker(
    tmp_path: Path, settings: Settings, fake_restic: FakeBinary
) -> None:
    mount = tmp_path / "update-backup"
    mount.mkdir()
    settings.BRIEVIE_BACKUP_DIR_SET = True
    settings.BRIEVIE_BACKUP_MOUNT = str(mount)
    settings.BRIEVIE_BACKUP_PASSWORD = PASSWORD
    destination.initialise(allow_same_disk=True, owner_uid=None, fallback=True)
    marker = json.loads((mount / destination.MARKER_NAME).read_text())
    assert marker["purpose"] == destination.UPDATE_FALLBACK_PURPOSE
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=True, owner_uid=None, fallback=True)
    assert caught.value.code == "already_initialised"


def test_initialise_never_turns_a_destination_into_the_fallback(
    configured_folder: Path, fake_restic: FakeBinary
) -> None:
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=True, owner_uid=None, fallback=True)
    assert caught.value.code == "update_backup_unavailable"
    assert fake_restic.calls == []


def test_initialise_of_the_fallback_needs_a_folder(settings: Settings) -> None:
    settings.BRIEVIE_BACKUP_DIR_SET = False
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://backup.example/brievie"
    settings.BRIEVIE_BACKUP_PASSWORD = PASSWORD
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=True, owner_uid=None, fallback=True)
    assert caught.value.code == "update_backup_unavailable"


# --- the run itself --------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_run_now_writes_the_fallback_with_the_label(
    fallback_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    answer_backups(fake_restic)

    run = services.run_update_fallback(label=LABEL)

    assert run.status == BackupRun.Status.SUCCEEDED
    assert run.destination_kind == destination.UPDATE_FALLBACK_KIND
    assert run.label == LABEL
    assert run.requested_by == services.UPDATE_SAFETY
    backups = [call for call in fake_restic.calls if call[0] == "backup"]
    assert len(backups) == 2
    assert all(f"label:{LABEL}" in call and restic.SAFETY_TAG in call for call in backups)
    forgets = [call for call in fake_restic.calls if call[0] == "forget"]
    assert forgets == [restic.fallback_forget_argv(services.backup_host())]


@pytest.mark.django_db(transaction=True)
def test_run_now_refuses_a_real_destination(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = services.run_update_fallback(label=LABEL)
    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "update_backup_unavailable"
    assert all(call[0] != "backup" for call in fake_restic.calls)


@pytest.mark.django_db(transaction=True)
def test_run_now_refuses_a_bad_label(fallback_folder: Path) -> None:
    with pytest.raises(BackupError) as caught:
        services.run_update_fallback(label="latest")
    assert caught.value.code == "label_invalid"
    assert not BackupRun.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_run_now_failure_is_recorded_with_its_reason(
    fallback_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    fake_restic.respond(
        ("backup", "--json"), stderr=f"Fatal: {fallback_folder} no space left on device", code=1
    )

    run = services.run_update_fallback(label=LABEL)

    assert run.status == BackupRun.Status.FAILED
    assert run.reason_code == "destination_full"
    assert str(fallback_folder) not in run.reason
    assert PASSWORD not in run.reason


@pytest.mark.django_db(transaction=True)
def test_run_now_puts_no_secret_in_argv(
    settings: Settings,
    fallback_folder: Path,
    fake_restic: FakeBinary,
    fake_pg_dump: FakeBinary,
) -> None:
    answer_backups(fake_restic)
    services.run_update_fallback(label=LABEL)
    calls = [*fake_restic.calls, *fake_pg_dump.calls]
    database = settings.DATABASE_BACKUP or {}
    secrets = [PASSWORD, str(database.get("PASSWORD") or "x" * 64)]
    assert not any(secret in arg for call in calls for arg in call for secret in secrets)


@pytest.mark.django_db(transaction=True)
def test_a_fallback_run_does_not_count_in_status(
    fallback_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    answer_backups(fake_restic)
    services.run_update_fallback(label=LABEL)
    failed = BackupRun.objects.create(
        kind=BackupRun.Kind.BACKUP,
        status=BackupRun.Status.FAILED,
        destination_kind=destination.UPDATE_FALLBACK_KIND,
        requested_by=services.UPDATE_SAFETY,
    )
    interrupted = BackupRun.objects.create(
        kind=BackupRun.Kind.BACKUP,
        status=BackupRun.Status.INTERRUPTED,
        destination_kind=destination.UPDATE_FALLBACK_KIND,
    )

    status = services.status_summary()

    assert status.last_success is None
    assert status.last_failure is None
    assert status.last_interrupted is None
    assert failed.id
    assert interrupted.id


@pytest.mark.django_db(transaction=True)
def test_a_destination_run_with_a_label_counts(
    configured_folder: Path, fake_restic: FakeBinary, fake_pg_dump: FakeBinary
) -> None:
    run = services.request_run("backup", requested_by=services.UPDATE_SAFETY, label=LABEL)
    answer_backups(fake_restic)

    services.perform_run(run.id)

    run.refresh_from_db()
    assert run.status == BackupRun.Status.SUCCEEDED
    assert run.label == LABEL
    assert services.status_summary().last_success == run
    backups = [call for call in fake_restic.calls if call[0] == "backup"]
    assert all(f"label:{LABEL}" in call and restic.SAFETY_TAG in call for call in backups)
    # A safety back-up never prunes the destination in its own run.
    assert all(call[0] != "forget" for call in fake_restic.calls)


def _finished(status: str) -> Callable[..., BackupRun]:
    def wait(run_id: UUID, **kwargs: object) -> BackupRun:
        BackupRun.objects.filter(id=run_id).update(status=status)
        return BackupRun.objects.get(id=run_id)

    return wait


def test_backup_command_takes_a_label_for_the_safety_backup(
    configured_folder: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(services, "wait_for_run", _finished(BackupRun.Status.SUCCEEDED))
    monkeypatch.setenv("BRIEVIE_WRAPPER", "1")
    call_command("backup", "--safety", "--label", LABEL)
    run = BackupRun.objects.get()
    assert (run.requested_by, run.label) == (services.UPDATE_SAFETY, LABEL)
    assert f"safety_run_id={run.id}" in capsys.readouterr().out.splitlines()
    with pytest.raises(CommandError):
        call_command("backup", "--label", LABEL)
    with pytest.raises(CommandError):
        call_command("backup", "--safety", "--label", "latest")


def test_update_backup_command_prints_the_run_id(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    made = BackupRun(
        id=uuid4(),
        kind=BackupRun.Kind.BACKUP,
        status=BackupRun.Status.SUCCEEDED,
        label=LABEL,
        size_bytes=2048,
    )
    monkeypatch.setattr(services, "run_update_fallback", lambda *, label: made)
    monkeypatch.setenv("BRIEVIE_WRAPPER", "1")
    out = io.StringIO()
    call_command("update_backup", "--label", LABEL, stdout=out)
    assert f"run_id={made.id}" in out.getvalue().splitlines()


def test_update_backup_command_fails_with_the_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    made = BackupRun(
        id=uuid4(),
        kind=BackupRun.Kind.BACKUP,
        status=BackupRun.Status.FAILED,
        reason_code="destination_full",
        reason=str(REASONS["destination_full"]),
    )
    monkeypatch.setattr(services, "run_update_fallback", lambda *, label: made)
    with pytest.raises(CommandError) as caught:
        call_command("update_backup", "--label", LABEL)
    assert caught.value.returncode == 1
    assert str(REASONS["destination_full"]) in str(caught.value)


# --- finding a back-up by its label ----------------------------------------------------------


def snapshot(run_id: UUID, kind: str, when: datetime, *tags: str) -> dict[str, object]:
    return {
        "id": uuid4().hex,
        "time": when.astimezone(UTC).isoformat(),
        "tags": [f"run:{run_id}", f"kind:{kind}", *tags],
        "paths": ["/x"],
    }


def answer_snapshots(
    fake_restic: FakeBinary, db: list[dict[str, object]], files: list[dict[str, object]]
) -> None:
    fake_restic.respond(
        tuple(restic.snapshots_argv("kind:db", no_lock=True)), stdout=json.dumps(db).encode()
    )
    fake_restic.respond(
        tuple(restic.snapshots_argv("kind:files", no_lock=True)),
        stdout=json.dumps(files).encode(),
    )


def test_find_run_by_label_picks_the_newest_labelled_run(
    configured_folder: Path, fake_restic: FakeBinary
) -> None:
    now = timezone.now()
    older, newer, unlabelled = uuid4(), uuid4(), uuid4()
    label = f"label:{LABEL}"
    db = [
        snapshot(older, "db", now - timedelta(days=2), label),
        snapshot(newer, "db", now - timedelta(days=1), label),
        snapshot(unlabelled, "db", now),
    ]
    files = [
        snapshot(older, "files", now - timedelta(days=2), label),
        snapshot(newer, "files", now - timedelta(days=1), label),
        snapshot(unlabelled, "files", now),
    ]
    answer_snapshots(fake_restic, db, files)

    found = restore.find_run(destination.configured_repository(), PASSWORD, LABEL)

    assert found.run_id == newer


def test_find_run_by_unknown_label_is_run_not_found(
    configured_folder: Path, fake_restic: FakeBinary
) -> None:
    now = timezone.now()
    run_id = uuid4()
    answer_snapshots(
        fake_restic,
        [snapshot(run_id, "db", now, "label:pre-update-0.2.9")],
        [snapshot(run_id, "files", now, "label:pre-update-0.2.9")],
    )
    with pytest.raises(BackupError) as caught:
        restore.find_run(destination.configured_repository(), PASSWORD, LABEL)
    assert caught.value.code == "run_not_found"


def test_find_run_refuses_a_malformed_name(
    configured_folder: Path, fake_restic: FakeBinary
) -> None:
    with pytest.raises(BackupError) as caught:
        restore.find_run(destination.configured_repository(), PASSWORD, "pre-update-latest")
    assert caught.value.code == "run_not_found"
    assert fake_restic.calls == []

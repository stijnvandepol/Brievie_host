from apps.backup.errors import REASONS, BackupError
from apps.backup.models import OPEN_STATUSES, BackupRun
from apps.common.models import WorkspaceModel

EXPECTED_CODES = {
    "not_configured",
    "backup_running",
    "destination_conflict",
    "destination_not_writable",
    "destination_not_ready",
    "same_disk",
    "already_initialised",
    "repository_has_credentials",
    "repository_unsupported",
    "repository_missing",
    "wrong_password",
    "repository_locked",
    "destination_unreachable",
    "destination_full",
    "files_incomplete",
    "dump_failed",
    "backup_role_missing",
    "pg_client_too_old",
    "no_backup_password",
    "busy",
    "env_copy_has_secret",
    "manifest_unreadable",
    "master_key_unverified",
    "newer_backup",
    "run_not_found",
    "wrong_master_key",
    "restore_failed",
    "interrupted_by_restore",
    "internal_error",
    "restic_failed",
    "abandoned",
    "backup_password_unwritable",
    "backup_folder_unwritable",
    "master_key_malformed",
    "hostname_unusable",
    "documents_incomplete",
    "unsafe_path",
    "directory_not_allowed",
    "out_dir_not_empty",
    "restore_target_unsafe",
    "caddy_not_ready",
    "clock_skew",
    "retention_incomplete",
    "env_copy_missing",
    "not_in_container",
    "label_invalid",
    "update_backup_unavailable",
}


def test_backup_run_defaults() -> None:
    run = BackupRun.objects.create(kind=BackupRun.Kind.BACKUP)
    run.refresh_from_db()
    assert run.status == BackupRun.Status.QUEUED
    assert run.requested_by == "cli"
    assert run.size_bytes is None
    assert run.reason == ""
    assert run.reason_code == ""
    assert run.destination_kind == ""
    assert run.label == ""
    assert run.started_at is None
    assert run.finished_at is None


def test_open_statuses_are_queued_and_running() -> None:
    assert OPEN_STATUSES == (BackupRun.Status.QUEUED, BackupRun.Status.RUNNING)


def test_backup_run_is_not_a_workspace_table() -> None:
    assert not issubclass(BackupRun, WorkspaceModel)
    BackupRun.objects.create(kind=BackupRun.Kind.CHECK)
    # No workspace_context is active here, and the row is still readable.
    assert BackupRun.objects.filter(kind=BackupRun.Kind.CHECK).count() == 1


def test_every_reason_code_has_a_dutch_sentence() -> None:
    assert set(REASONS) == EXPECTED_CODES
    for code in EXPECTED_CODES:
        error = BackupError(code)
        assert error.code == code
        assert error.message.strip()
    assert BackupError("not_configured").message == "Er is nog geen back-up ingesteld."
    assert BackupError("interrupted_by_restore").message == "Afgebroken door herstel."
    assert BackupError("abandoned").message == (
        "De vorige back-up is afgebroken. Er is een nieuwe gestart."
    )
    assert BackupError("files_incomplete").message.startswith("De back-up is gemaakt, maar")

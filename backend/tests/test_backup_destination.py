import json
import os
from pathlib import Path
from unittest import mock

import pytest

from apps.backup import destination
from apps.backup.errors import BackupError
from tests.backup_support import FakeBinary

PASSWORD = "p" * 40


@pytest.fixture
def folder(settings: object, tmp_path: Path) -> Path:
    """A back-up folder as the service sees it: `/backup` mounted from the host."""
    mount = tmp_path / "backup"
    mount.mkdir()
    settings.BRIEVIE_BACKUP_DIR_SET = True  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_MOUNT = str(mount)  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_PASSWORD = PASSWORD  # type: ignore[attr-defined]
    return mount


def _other_disk(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    mount = str(folder)
    monkeypatch.setattr(destination, "_device", lambda path: 2 if str(path) == mount else 1)


def _write_marker(folder: Path, content: str | None = None) -> None:
    marker = {"format": 1, "created_at": "2026-01-01T00:00:00+00:00", "repository": "restic"}
    (folder / destination.MARKER_NAME).write_text(content or json.dumps(marker))


def test_nothing_configured_is_not_configured(settings: object) -> None:
    with pytest.raises(BackupError) as caught:
        destination.configured_repository()
    assert caught.value.code == "not_configured"
    assert caught.value.message == "Er is nog geen back-up ingesteld."
    assert destination.destination_kind() == ""


def test_folder_without_marker_is_refused(folder: Path, fake_restic: FakeBinary) -> None:
    # The disk is not plugged in: the mount is an empty directory on the system disk.
    with pytest.raises(BackupError) as caught:
        destination.configured_repository()
    assert caught.value.code == "destination_not_ready"
    assert fake_restic.calls == []
    assert list(folder.iterdir()) == []


def test_missing_mount_is_not_ready_either(
    settings: object, tmp_path: Path, fake_restic: FakeBinary
) -> None:
    settings.BRIEVIE_BACKUP_DIR_SET = True  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_MOUNT = str(tmp_path / "nope")  # type: ignore[attr-defined]
    with pytest.raises(BackupError) as caught:
        destination.configured_repository()
    assert caught.value.code == "destination_not_ready"
    assert not (tmp_path / "nope").exists()


def test_folder_with_marker_points_at_the_restic_dir(folder: Path) -> None:
    _write_marker(folder)
    repository = destination.configured_repository()
    assert repository.kind == "folder"
    assert repository.location == f"{folder}/restic"
    assert repository.credentials == {}


def test_init_writes_marker_and_initialises(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    repository = destination.initialise(allow_same_disk=False, owner_uid=None)
    assert fake_restic.calls == [["init"]]
    assert repository.location == f"{folder}/restic"
    marker = json.loads((folder / destination.MARKER_NAME).read_text())
    assert marker["format"] == 1
    assert marker["repository"] == "restic"
    assert marker["created_at"]
    assert (folder / destination.MARKER_NAME).stat().st_mode & 0o777 == 0o644
    assert destination.configured_repository().location == f"{folder}/restic"


def test_init_does_not_write_a_marker_when_restic_fails(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    fake_restic.respond(("init",), stderr="boom", code=1)
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=False, owner_uid=None)
    assert caught.value.code == "restic_failed"
    assert not (folder / destination.MARKER_NAME).exists()


def test_init_twice_is_refused(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    destination.initialise(allow_same_disk=False, owner_uid=None)
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=False, owner_uid=None)
    assert caught.value.code == "already_initialised"
    assert fake_restic.calls == [["init"]]


def test_init_on_the_disk_of_the_documents_is_refused(
    folder: Path, settings: object, tmp_path: Path, fake_restic: FakeBinary
) -> None:
    storage = tmp_path / "documents"
    storage.mkdir()
    settings.BRIEVIE_STORAGE_ROOT = str(storage)  # type: ignore[attr-defined]
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=False, owner_uid=None)
    assert caught.value.code == "same_disk"
    assert fake_restic.calls == []
    assert list(folder.iterdir()) == []
    destination.initialise(allow_same_disk=True, owner_uid=None)
    assert (folder / destination.MARKER_NAME).exists()


def test_init_hands_the_tree_to_the_keeper(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    # The fake restic creates nothing, so make the tree the real one would.
    (folder / destination.REPOSITORY_DIR / "data").mkdir(parents=True)
    with mock.patch.object(os, "chown") as chown:
        destination.initialise(allow_same_disk=False, owner_uid=10001)
    owned = {Path(call.args[0]) for call in chown.call_args_list}
    assert folder / destination.MARKER_NAME in owned
    assert folder / destination.REPOSITORY_DIR in owned
    assert folder / destination.REPOSITORY_DIR / "data" in owned
    assert all(call.args[1:] == (10001, 10001) for call in chown.call_args_list)
    tree = [c for c in chown.call_args_list if Path(c.args[0]) != folder / destination.MARKER_NAME]
    assert all(call.kwargs == {"follow_symlinks": False} for call in tree)  # links: never followed


def test_init_without_owner_does_not_chown(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    with mock.patch.object(os, "chown") as chown:
        destination.initialise(allow_same_disk=False, owner_uid=None)
    chown.assert_not_called()


def test_init_of_a_remote_repository_maps_already_exists(
    settings: object, fake_restic: FakeBinary
) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "s3:https://s3.example.com/bucket"  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_PASSWORD = PASSWORD  # type: ignore[attr-defined]
    fake_restic.respond(
        ("init",), stderr="Fatal: create repository: config file already exists", code=1
    )
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=False, owner_uid=None)
    assert caught.value.code == "already_initialised"


def test_init_of_a_remote_repository(settings: object, fake_restic: FakeBinary) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://backup.example.com/repo"  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_PASSWORD = PASSWORD  # type: ignore[attr-defined]
    repository = destination.initialise(allow_same_disk=False, owner_uid=None)
    assert fake_restic.calls == [["init"]]
    assert repository.kind == "rest"


def test_rest_credentials_go_into_their_own_variables(settings: object) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://backup.example.com/repo"  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_REST_USERNAME = "stijn"  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_REST_PASSWORD = "rest-secret"  # type: ignore[attr-defined]
    repository = destination.configured_repository()
    assert repository.kind == "rest"
    assert "@" not in repository.location
    assert repository.credentials == {
        "RESTIC_REST_USERNAME": "stijn",
        "RESTIC_REST_PASSWORD": "rest-secret",
    }


def test_s3_credentials_go_into_their_own_variables(settings: object) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "s3:https://s3.example.com/bucket"  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_S3_ACCESS_KEY_ID = "AKIA"  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY = "s3-secret"  # type: ignore[attr-defined]
    repository = destination.configured_repository()
    assert repository.kind == "s3"
    assert repository.credentials == {
        "AWS_ACCESS_KEY_ID": "AKIA",
        "AWS_SECRET_ACCESS_KEY": "s3-secret",
    }


def test_repository_with_credentials_in_the_address_is_refused(settings: object) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://user:pw@backup.example.com/repo"  # type: ignore[attr-defined]
    with pytest.raises(BackupError) as caught:
        destination.configured_repository()
    assert caught.value.code == "repository_has_credentials"


def test_unsupported_repository_is_refused(settings: object) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "sftp:user@host:/repo"  # type: ignore[attr-defined]
    with pytest.raises(BackupError) as caught:
        destination.configured_repository()
    assert caught.value.code == "repository_unsupported"


def test_folder_and_repository_together_are_refused(folder: Path, settings: object) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://backup.example.com/repo"  # type: ignore[attr-defined]
    with pytest.raises(BackupError) as caught:
        destination.configured_repository()
    assert caught.value.code == "destination_conflict"
    assert "één" in caught.value.message


def test_placeholder_backup_password_is_refused(settings: object) -> None:
    settings.BRIEVIE_BACKUP_PASSWORD = "change-me-backup-password"  # type: ignore[attr-defined]
    with pytest.raises(BackupError) as caught:
        destination.backup_password()
    assert caught.value.code == "no_backup_password"


def test_empty_backup_password_is_the_first_run_state(settings: object) -> None:
    settings.BRIEVIE_BACKUP_PASSWORD = ""  # type: ignore[attr-defined]
    with pytest.raises(BackupError) as caught:
        destination.backup_password()
    assert caught.value.code == "no_backup_password"


def test_secrets_lists_every_credential(settings: object) -> None:
    settings.BRIEVIE_BACKUP_PASSWORD = PASSWORD  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY = "s3-secret"  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_REST_PASSWORD = "rest-secret"  # type: ignore[attr-defined]
    settings.DATABASE_BACKUP = {"PASSWORD": "db-secret"}  # type: ignore[attr-defined]
    found = destination.secrets()
    for value in (PASSWORD, "s3-secret", "rest-secret", "db-secret"):
        assert value in found
    assert "" not in found


def test_restic_gets_the_password_in_the_environment_only(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    destination.initialise(allow_same_disk=False, owner_uid=None)
    assert all(PASSWORD not in arg for call in fake_restic.calls for arg in call)
    assert "RESTIC_PASSWORD" in fake_restic.env_keys[0]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"format": 2, "repository": "restic"}',
        '{"format": 1, "repository": "elsewhere"}',
        "{}",
    ],
)
def test_foreign_or_corrupt_marker_is_not_ready(
    folder: Path, fake_restic: FakeBinary, content: str
) -> None:
    _write_marker(folder, content)
    with pytest.raises(BackupError) as caught:
        destination.configured_repository()
    assert caught.value.code == "destination_not_ready"


def test_marker_write_failure_leaves_no_marker(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    monkeypatch.setattr(Path, "write_text", mock.Mock(side_effect=PermissionError))
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=False, owner_uid=None)
    assert caught.value.code == "destination_not_writable"
    assert not (folder / destination.MARKER_NAME).exists()


def test_chown_failure_leaves_no_marker(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    (folder / destination.REPOSITORY_DIR).mkdir()
    with (
        mock.patch.object(os, "chown", side_effect=PermissionError),
        pytest.raises(BackupError) as caught,
    ):
        destination.initialise(allow_same_disk=False, owner_uid=10001)
    assert caught.value.code == "destination_not_writable"
    assert not (folder / destination.MARKER_NAME).exists()


def test_marker_is_removed_when_its_chown_fails(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    (folder / destination.REPOSITORY_DIR).mkdir()
    marker = folder / destination.MARKER_NAME

    def chown(path: str | Path, uid: int, gid: int, *, follow_symlinks: bool = True) -> None:
        if Path(path) == marker:
            raise PermissionError

    with (
        mock.patch.object(os, "chown", side_effect=chown),
        pytest.raises(BackupError) as caught,
    ):
        destination.initialise(allow_same_disk=False, owner_uid=10001)
    assert caught.value.code == "destination_not_writable"
    assert not marker.exists()


def test_marker_is_written_after_the_tree_is_handed_over(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    (folder / destination.REPOSITORY_DIR).mkdir()
    seen: list[bool] = []

    def chown(path: str | Path, uid: int, gid: int, *, follow_symlinks: bool = True) -> None:
        seen.append((folder / destination.MARKER_NAME).exists())

    with mock.patch.object(os, "chown", side_effect=chown):
        destination.initialise(allow_same_disk=False, owner_uid=10001)
    assert seen == [False, True]  # the tree first, the marker last


def test_second_init_completes_a_half_finished_one(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    fake_restic.respond(("init",), stderr="Fatal: config file already exists", code=1)
    destination.initialise(allow_same_disk=False, owner_uid=None)
    assert fake_restic.calls == [["init"], ["cat", "config"]]
    assert destination.configured_repository().location == f"{folder}/restic"


def test_existing_repository_with_another_password_is_refused(
    folder: Path, fake_restic: FakeBinary, monkeypatch: pytest.MonkeyPatch
) -> None:
    _other_disk(monkeypatch, folder)
    fake_restic.respond(("init",), stderr="Fatal: config file already exists", code=1)
    fake_restic.respond(("cat", "config"), stderr="wrong password", code=12)
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=False, owner_uid=None)
    assert caught.value.code == "wrong_password"
    assert not (folder / destination.MARKER_NAME).exists()


def test_missing_storage_root_is_not_ready(
    folder: Path, settings: object, tmp_path: Path, fake_restic: FakeBinary
) -> None:
    settings.BRIEVIE_STORAGE_ROOT = str(tmp_path / "gone")  # type: ignore[attr-defined]
    with pytest.raises(BackupError) as caught:
        destination.initialise(allow_same_disk=False, owner_uid=None)
    assert caught.value.code == "destination_not_ready"
    assert fake_restic.calls == []

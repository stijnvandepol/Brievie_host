"""Where a back-up goes, and the guards against writing it to the wrong place.

A folder destination is the host's back-up disk mounted at `BRIEVIE_BACKUP_MOUNT`.
When that disk is not plugged in, the mount is an empty directory on the system
disk. Only `initialise` may put anything there; every other path refuses unless
the marker file written by `initialise` is present, and never creates anything.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings

from apps.backup import restic
from apps.backup.errors import BackupError

MARKER_NAME = "brievie-backup.json"
REPOSITORY_DIR = "restic"
MARKER_FORMAT = 1

_ALREADY_EXISTS = ("already exists", "config file already exists")


def destination_kind() -> str:
    """`folder`, `rest`, `s3`, or `` when nothing is set."""
    repository: str = settings.BRIEVIE_BACKUP_REPOSITORY.strip()
    if settings.BRIEVIE_BACKUP_DIR_SET:
        if repository:
            raise BackupError("destination_conflict")
        return "folder"
    if not repository:
        return ""
    restic.check_repository_value(repository)
    return "rest" if repository.startswith("rest:") else "s3"


def _mount() -> Path:
    return Path(settings.BRIEVIE_BACKUP_MOUNT)


def _marker() -> Path:
    return _mount() / MARKER_NAME


def _device(path: Path | str) -> int:
    return os.stat(path).st_dev


def _remote_repository(kind: str) -> restic.Repository:
    location: str = settings.BRIEVIE_BACKUP_REPOSITORY.strip()
    if kind == "rest":
        pairs = {
            "RESTIC_REST_USERNAME": settings.BRIEVIE_BACKUP_REST_USERNAME,
            "RESTIC_REST_PASSWORD": settings.BRIEVIE_BACKUP_REST_PASSWORD,
        }
    else:
        pairs = {
            "AWS_ACCESS_KEY_ID": settings.BRIEVIE_BACKUP_S3_ACCESS_KEY_ID,
            "AWS_SECRET_ACCESS_KEY": settings.BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY,
        }
    credentials = {name: value for name, value in pairs.items() if value}
    return restic.Repository(
        kind="rest" if kind == "rest" else "s3", location=location, credentials=credentials
    )


def _folder_repository() -> restic.Repository:
    return restic.Repository(kind="folder", location=str(_mount() / REPOSITORY_DIR), credentials={})


def configured_repository() -> restic.Repository:
    """The repository to back up to. Read-only: it never creates anything."""
    kind = destination_kind()
    if not kind:
        raise BackupError("not_configured")
    if kind == "folder":
        if not _marker().is_file():
            raise BackupError("destination_not_ready")
        return _folder_repository()
    return _remote_repository(kind)


def backup_password() -> str:
    password: str = settings.BRIEVIE_BACKUP_PASSWORD
    if not password or "change-me" in password:
        raise BackupError("no_backup_password")
    return password


def secrets() -> list[str]:
    """Every value `restic.redact` must hide."""
    database = settings.DATABASE_BACKUP or {}
    values = [
        settings.BRIEVIE_BACKUP_PASSWORD,
        settings.BRIEVIE_BACKUP_S3_ACCESS_KEY_ID,
        settings.BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY,
        settings.BRIEVIE_BACKUP_REST_USERNAME,
        settings.BRIEVIE_BACKUP_REST_PASSWORD,
        str(database.get("PASSWORD") or ""),
    ]
    return [value for value in values if value]


def _chown_tree(root: Path, uid: int) -> None:
    os.chown(root, uid, uid)
    for directory, names, files in os.walk(root):
        for name in (*names, *files):
            os.chown(Path(directory) / name, uid, uid)


def _run_init(repository: restic.Repository) -> None:
    env = restic.restic_env(repository, backup_password())
    result = restic.run(restic.init_argv(), env, secrets=secrets())
    if result.returncode == 1 and any(text in result.stderr.lower() for text in _ALREADY_EXISTS):
        raise BackupError("already_initialised")
    restic.raise_for(result)


def initialise(*, allow_same_disk: bool, owner_uid: int | None) -> restic.Repository:
    """Create the repository: the only place that may write to an empty destination.

    `owner_uid` is the keeper's uid; the one-off container runs as root, so the
    files it creates are handed over. Tests pass None.
    """
    kind = destination_kind()
    if not kind:
        raise BackupError("not_configured")
    backup_password()
    if kind != "folder":
        repository = _remote_repository(kind)
        _run_init(repository)
        return repository

    mount = _mount()
    if not mount.is_dir():
        raise BackupError("destination_not_ready")
    if _marker().exists():
        raise BackupError("already_initialised")
    if not allow_same_disk and _device(mount) == _device(settings.BRIEVIE_STORAGE_ROOT):
        raise BackupError("same_disk")
    repository = _folder_repository()
    _run_init(repository)
    marker = {
        "format": MARKER_FORMAT,
        "created_at": datetime.now(UTC).isoformat(),
        "repository": REPOSITORY_DIR,
    }
    _marker().write_text(json.dumps(marker))
    _marker().chmod(0o644)
    if owner_uid is not None:
        _chown_tree(mount / REPOSITORY_DIR, owner_uid)
        os.chown(_marker(), owner_uid, owner_uid)
    return repository

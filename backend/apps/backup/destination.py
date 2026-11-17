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

_ALREADY_EXISTS = "config file already exists"


def destination_kind() -> str:
    """`folder`, `rest`, `s3`, or `` when nothing is set.

    Raises BackupError: `destination_conflict` (folder and repository both set),
    `repository_unsupported` or `repository_has_credentials` (bad repository value).
    """
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


def _marker_is_valid() -> bool:
    try:
        content = json.loads(_marker().read_text())
    except (OSError, ValueError):
        return False
    return (
        isinstance(content, dict)
        and content.get("format") == MARKER_FORMAT
        and content.get("repository") == REPOSITORY_DIR
    )


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
        if not _marker_is_valid():
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


def _run_init(repository: restic.Repository) -> bool:
    """Run `restic init`; True when the repository already existed."""
    env = restic.restic_env(repository, backup_password())
    result = restic.run(restic.init_argv(), env, secrets=secrets())
    if result.returncode == 1 and _ALREADY_EXISTS in result.stderr.lower():
        return True
    restic.raise_for(result)
    return False


def _verify_password(repository: restic.Repository) -> None:
    env = restic.restic_env(repository, backup_password())
    restic.raise_for(restic.run(restic.cat_config_argv(), env, secrets=secrets()))


def _finish_folder(owner_uid: int | None) -> None:
    """Hand the tree over, then write the marker last: it means "ready"."""
    marker = _marker()
    try:
        if owner_uid is not None:
            _chown_tree(_mount() / REPOSITORY_DIR, owner_uid)
        content = {
            "format": MARKER_FORMAT,
            "created_at": datetime.now(UTC).isoformat(),
            "repository": REPOSITORY_DIR,
        }
        marker.write_text(json.dumps(content))
        marker.chmod(0o644)
        if owner_uid is not None:
            os.chown(marker, owner_uid, owner_uid)
    except OSError as error:
        marker.unlink(missing_ok=True)
        raise BackupError("destination_not_writable") from error


def initialise(*, allow_same_disk: bool, owner_uid: int | None) -> restic.Repository:
    """Create the repository: the only place that may write to an empty destination.

    `owner_uid` is the keeper's uid; the one-off container runs as root, so the
    files it creates are handed over. Tests pass None.

    The same-disk check compares device ids. It is best-effort: it catches the
    unplugged disk (an empty directory on the root filesystem) but can miss
    Docker Desktop, btrfs subvolumes and a separate /var/lib/docker.

    A folder is ready only when the marker exists, and the marker is written
    last, so a half-finished run is completed by running this again.
    """
    kind = destination_kind()
    if not kind:
        raise BackupError("not_configured")
    backup_password()
    if kind != "folder":
        repository = _remote_repository(kind)
        if _run_init(repository):
            raise BackupError("already_initialised")
        return repository

    mount = _mount()
    if not mount.is_dir():
        raise BackupError("destination_not_ready")
    if _marker().exists():
        raise BackupError("already_initialised")
    if not allow_same_disk:
        try:
            same = _device(mount) == _device(settings.BRIEVIE_STORAGE_ROOT)
        except OSError as error:
            raise BackupError("destination_not_ready") from error
        if same:
            raise BackupError("same_disk")
    repository = _folder_repository()
    if _run_init(repository):
        # A half-finished earlier run, or a re-plugged disk: take it over only with our password.
        _verify_password(repository)
    _finish_folder(owner_uid)
    return repository

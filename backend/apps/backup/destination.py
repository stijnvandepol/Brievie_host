"""Where a back-up goes, and the guards against writing it to the wrong place.

A folder destination is the host's back-up disk mounted at `BRIEVIE_BACKUP_MOUNT`.
When that disk is not plugged in, the mount is an empty directory on the system
disk. Only `initialise` may put anything there; every other path refuses unless
the marker file written by `initialise` is present, and never creates anything.
"""

import contextlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings

from apps.backup import restic, safepaths
from apps.backup.errors import BackupError

logger = logging.getLogger(__name__)

MARKER_NAME = "brievie-backup.json"
REPOSITORY_DIR = "restic"
MARKER_FORMAT = 1
# An update's emergency back-up on a server without a destination: the Docker volume
# `update-backup`, mounted at /backup in the one-off admin container for that one call
# (./brievie). Its marker says so, and the two kinds of run never cross: an ordinary run
# (the keeper's, which prunes) refuses it, and the fallback run (which keeps only its
# newest run) refuses anything else. A restore reads it like any folder.
UPDATE_FALLBACK_PURPOSE = "update-fallback"
# The destination kind recorded on such a run; `status` does not count it.
UPDATE_FALLBACK_KIND = "update"

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
    """The back-up mount: `/backup` (or one in the test root), or refused."""
    return safepaths.require_backup_mount(safepaths.configured_path(settings.BRIEVIE_BACKUP_MOUNT))


def _marker() -> Path:
    return _mount() / MARKER_NAME


def _device(path: Path | str) -> int:
    return os.stat(path).st_dev


def _marker_content() -> dict[str, object] | None:
    """The marker when it is valid (our format, our repository folder), else None."""
    try:
        content = json.loads(_marker().read_text())
    except (OSError, ValueError):
        return None
    if (
        isinstance(content, dict)
        and content.get("format") == MARKER_FORMAT
        and content.get("repository") == REPOSITORY_DIR
    ):
        return content
    return None


def _marker_is_valid() -> bool:
    return _marker_content() is not None


def _is_fallback() -> bool:
    content = _marker_content()
    return content is not None and content.get("purpose") == UPDATE_FALLBACK_PURPOSE


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


def backup_repository(*, fallback: bool) -> restic.Repository:
    """The repository a back-up run writes to.

    `fallback`: the run of an update without a destination, which keeps only its newest
    run; anything but the fallback folder is refused (`update_backup_unavailable`). An
    ordinary run refuses the fallback folder (`destination_not_ready`), so the keeper's
    retention can never reach an update's way back.
    """
    if fallback:
        if destination_kind() != "folder" or not _is_fallback():
            raise BackupError("update_backup_unavailable")
        return _folder_repository()
    repository = configured_repository()
    if repository.kind == "folder" and _is_fallback():
        raise BackupError("destination_not_ready")
    return repository


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
    """Hand `root` and everything below it to `uid`; only ever inside the mount.

    `root` must lie strictly inside the mount (`unsafe_path` otherwise). It is opened
    component by component without following a link and walked through descriptors:
    no link is followed or handed over, and a file with more than one hard link is
    skipped (counted in the log).
    """
    safepaths.require_inside(root, _mount())
    expected = safepaths.identity(root)
    if expected is None:
        return  # nothing there to hand over
    descriptor = safepaths.open_dir_nofollow(root, expect=expected)
    try:
        skipped = safepaths.chown_tree(descriptor, uid)
    finally:
        os.close(descriptor)
    if skipped:
        logger.warning("Back-up init: %s entries were not handed over (links)", skipped)


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


def _finish_folder(owner_uid: int | None, *, fallback: bool = False) -> None:
    """Hand the tree over, then write the marker last: it means "ready".

    The marker is written to a temporary file relative to a descriptor of the mount
    and renamed over the old one (never through a link); a failure removes only the
    temporary file this call made.
    """
    mount = _mount()
    safepaths.require_inside(_marker(), mount)
    content = {
        "format": MARKER_FORMAT,
        "created_at": datetime.now(UTC).isoformat(),
        "repository": REPOSITORY_DIR,
    }
    if fallback:
        content["purpose"] = UPDATE_FALLBACK_PURPOSE
    try:
        mount_fd = safepaths.open_dir_nofollow(mount, expect=safepaths.identity(mount))
    except OSError as error:
        raise BackupError("destination_not_writable") from error
    temporary = f".{MARKER_NAME}.tmp"
    created = False
    try:
        if owner_uid is not None:
            _chown_tree(mount / REPOSITORY_DIR, owner_uid)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=mount_fd)
        handle = safepaths.write_new_at(
            mount_fd, temporary, json.dumps(content).encode(), mode=0o644
        )
        created = True
        try:
            if owner_uid is not None:
                os.fchown(handle, owner_uid, owner_uid)
        finally:
            os.close(handle)
        # Atomic, relative to the mount's descriptor: replaces a corrupt marker (or a
        # link in its place) without following it.
        os.replace(temporary, MARKER_NAME, src_dir_fd=mount_fd, dst_dir_fd=mount_fd)
        created = False
    except OSError as error:
        if created:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=mount_fd)
        raise BackupError("destination_not_writable") from error
    finally:
        os.close(mount_fd)


def initialise(
    *, allow_same_disk: bool, owner_uid: int | None, fallback: bool = False
) -> restic.Repository:
    """Create the repository: the only place that may write to an empty destination.

    `owner_uid` is the keeper's uid; the one-off container runs as root, so the
    files it creates are handed over. Tests pass None.

    The same-disk check compares device ids. It is best-effort: it catches the
    unplugged disk (an empty directory on the root filesystem) but can miss
    Docker Desktop, btrfs subvolumes and a separate /var/lib/docker.

    A folder is ready only when the marker exists, and the marker is written
    last, so a half-finished run is completed by running this again.

    `fallback`: the volume of an update's emergency back-up (./brievie mounts it at
    /backup for this call). It is on the same disk by design; a folder that is already a
    destination of its own is never turned into one (`update_backup_unavailable`).
    """
    kind = destination_kind()
    if fallback:
        if kind != "folder":
            raise BackupError("update_backup_unavailable")
        if _marker_content() is not None:
            if _is_fallback():
                raise BackupError("already_initialised")
            raise BackupError("update_backup_unavailable")
        allow_same_disk = True
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
    # A valid marker: set up already. An invalid one (corrupt, foreign) is treated as a
    # missing one: the repository below is taken over only with the configured
    # password (wrong_password otherwise), and the marker is written anew.
    if _marker_is_valid():
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
    _finish_folder(owner_uid, fallback=fallback)
    return repository

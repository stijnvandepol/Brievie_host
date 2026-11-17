"""Find (and with a flag delete) sealed page files that no document refers to.

This deletes user data when it is wrong, so it is conservative:

- The known paths are read as the back-up role with row security off. A role that
  cannot bypass row-level security fails with an error; it never yields a partial
  list that would make live pages look like orphans.
- A candidate is a regular file (never a symlink) in the document layout
  `<uuid>/<uuid>/<n>.page` or a `.tmp-*` file next to it, and nothing else.
- The files on disk are listed FIRST and the known paths read AFTER. A page written
  between the two reads is then known instead of taken for an orphan.
- Files younger than `ORPHAN_MIN_AGE` are skipped, and deleting checks that again.
"""

import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import psycopg
from django.conf import settings
from django.utils import timezone

from apps.backup.database import backup_conninfo
from apps.backup.errors import BackupError
from apps.backup.locks import backup_lock
from apps.documents.storage import FileSystemStorage

ORPHAN_MIN_AGE = timedelta(hours=24)

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PAGE_NAME = re.compile(r"\d+\.page")
_TEMP_NAME = re.compile(r"\.tmp-[A-Za-z0-9_]+")
_UUID_NAME = re.compile(_UUID)
_LAYOUT = re.compile(rf"({_UUID})/({_UUID})/(\d+\.page|\.tmp-[A-Za-z0-9_]+)")


@dataclass(frozen=True)
class Orphan:
    path: str  # relative to the storage root, with `/`
    size: int
    modified_at: datetime


@dataclass(frozen=True)
class SweepResult:
    orphans: list[Orphan]
    deleted: int


@dataclass(frozen=True)
class _OnDisk:
    path: str
    size: int
    modified_at: datetime
    is_page: bool


def _modified(info: os.stat_result) -> datetime:
    return datetime.fromtimestamp(info.st_mtime, tz=UTC)


def _subdirectories(directory: Path) -> list[Path]:
    """Real subdirectories whose name is a UUID; symlinks are never followed."""
    try:
        with os.scandir(directory) as entries:
            return [
                Path(entry.path)
                for entry in entries
                if _UUID_NAME.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False)
            ]
    except FileNotFoundError:
        return []


def _scan_disk(root: Path) -> list[_OnDisk]:
    """Every regular file of the document layout, whatever its age."""
    found: list[_OnDisk] = []
    try:
        for workspace_dir in _subdirectories(root):
            for document_dir in _subdirectories(workspace_dir):
                with os.scandir(document_dir) as entries:
                    for entry in entries:
                        is_page = _PAGE_NAME.fullmatch(entry.name) is not None
                        if not (is_page or _TEMP_NAME.fullmatch(entry.name)):
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        found.append(
                            _OnDisk(
                                path=f"{workspace_dir.name}/{document_dir.name}/{entry.name}",
                                size=info.st_size,
                                modified_at=_modified(info),
                                is_page=is_page,
                            )
                        )
    except OSError:
        # The message would name a path; say only that it went wrong.
        raise BackupError("internal_error") from None
    return found


def _relative(stored: str, root: Path) -> str:
    """The stored path in the walker's form: relative to the root, with `/`."""
    normal = os.path.normpath(stored)
    if os.path.isabs(normal):
        for base in (root, root.resolve()):
            try:
                normal = Path(normal).relative_to(base).as_posix()
                break
            except ValueError:
                continue
    return PurePosixPath(normal).as_posix()


def _known_paths(conninfo: str, root: Path) -> set[str]:
    with psycopg.connect(conninfo) as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        # Without BYPASSRLS this fails instead of returning only some rows.
        conn.execute("SET row_security = off")
        rows = conn.execute("SELECT storage_path FROM documents_documentpage").fetchall()
    return {_relative(str(row[0]), root) for row in rows}


def find_orphans(*, conninfo: str, storage_root: Path, now: datetime) -> list[Orphan]:
    # Order matters: the disk first, the database after (see the module docstring).
    on_disk = _scan_disk(storage_root)
    known = _known_paths(conninfo, storage_root)
    pages_on_disk = {item.path for item in on_disk if item.is_page}
    if known and pages_on_disk and not (known & pages_on_disk):
        # Probably a path form this code does not read: refuse rather than list everything.
        raise BackupError("internal_error")
    cutoff = now - ORPHAN_MIN_AGE
    return sorted(
        (
            Orphan(path=item.path, size=item.size, modified_at=item.modified_at)
            for item in on_disk
            if item.path not in known and item.modified_at < cutoff
        ),
        key=lambda orphan: orphan.path,
    )


def _deletable(path: str, root: Path) -> bool:
    if _LAYOUT.fullmatch(path) is None:
        return False
    real_root = Path(os.path.realpath(root))
    target = real_root / path
    # The directories on the way must be real ones, not links out of the root.
    if Path(os.path.realpath(target.parent)) != target.parent:
        return False
    try:
        info = target.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and _modified(info) < timezone.now() - ORPHAN_MIN_AGE


def delete_orphans(orphans: Sequence[Orphan], storage_root: Path) -> int:
    """Delete what is still a regular, old file of the layout; returns how many went."""
    storage = FileSystemStorage(storage_root)
    deleted = 0
    for orphan in orphans:
        if not _deletable(orphan.path, storage_root):
            continue
        try:
            storage.delete(orphan.path)
        except OSError:
            raise BackupError("internal_error") from None
        deleted += 1
    return deleted


def sweep(*, delete: bool) -> SweepResult:
    root = Path(settings.BRIEVIE_STORAGE_ROOT)
    with backup_lock():
        orphans = find_orphans(conninfo=backup_conninfo(), storage_root=root, now=timezone.now())
        deleted = delete_orphans(orphans, root) if delete else 0
    return SweepResult(orphans=orphans, deleted=deleted)

"""Find (and with a flag delete) sealed page files that no document refers to.

This deletes user data when it is wrong, so it is conservative:

- The known paths are read as the back-up role with row security off. A role that
  cannot bypass row-level security fails with an error; it never yields a partial
  list that would make live pages look like orphans.
- A candidate is a regular file (never a symlink) in the document layout
  `<uuid>/<uuid>/<n>.page`, an uploaded file's sealed `<uuid>/<uuid>/original`, or a
  `.tmp-*` file next to them, and nothing else. An original is known when its document
  row says it has one (`original_kind` not empty).
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
from django.db import connection
from django.utils import timezone

from apps.backup import safepaths
from apps.backup.database import backup_conninfo
from apps.backup.errors import BackupError
from apps.backup.locks import backup_lock
from apps.documents.page_files import ORIGINAL_NAME

ORPHAN_MIN_AGE = timedelta(hours=24)

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PAGE_NAME = re.compile(r"[0-9]+\.page")
_TEMP_NAME = re.compile(r"\.tmp-[A-Za-z0-9_]+")
_UUID_NAME = re.compile(_UUID)
_LAYOUT = re.compile(rf"({_UUID})/({_UUID})/([0-9]+\.page|{ORIGINAL_NAME}|\.tmp-[A-Za-z0-9_]+)")
_PAGE_PATH = re.compile(rf"{_UUID}/{_UUID}/[0-9]+\.page")
_ORIGINAL_PATH = re.compile(rf"{_UUID}/{_UUID}/{ORIGINAL_NAME}")


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
    # A page or an original: a file a database row names (a temp file is not).
    is_stored: bool


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
                        is_stored = (
                            _PAGE_NAME.fullmatch(entry.name) is not None
                            or entry.name == ORIGINAL_NAME
                        )
                        if not (is_stored or _TEMP_NAME.fullmatch(entry.name)):
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
                                is_stored=is_stored,
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
        originals = conn.execute(
            "SELECT workspace_id, id FROM documents_document WHERE original_kind <> ''"
        ).fetchall()
    return {_relative(str(row[0]), root) for row in rows} | {
        f"{workspace_id}/{document_id}/{ORIGINAL_NAME}" for workspace_id, document_id in originals
    }


def find_orphans(*, conninfo: str, storage_root: Path, now: datetime) -> list[Orphan]:
    # Order matters: the disk first, the database after (see the module docstring).
    on_disk = _scan_disk(storage_root)
    known = _known_paths(conninfo, storage_root)
    if not all(_PAGE_PATH.fullmatch(path) or _ORIGINAL_PATH.fullmatch(path) for path in known):
        # A stored path in a form this code cannot place: its file would look orphaned.
        raise BackupError("internal_error")
    stored_on_disk = {item.path for item in on_disk if item.is_stored}
    if stored_on_disk and not (known & stored_on_disk):
        # No known path matches any page or original on disk: an empty or wrong database,
        # or a path form this code does not read. Refuse rather than list the whole store.
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


def _unlink_if_old(root: int, path: str) -> bool:
    """Unlink one layout file through directory descriptors; False when it is not ours to delete.

    `root` is a descriptor of the storage root. Every component below it is opened with
    O_NOFOLLOW relative to its parent, so a directory swapped for a symlink after the
    listing cannot redirect the unlink.
    """
    if _LAYOUT.fullmatch(path) is None:
        return False
    *directories, name = path.split("/")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    opened: list[int] = []
    try:
        for directory in directories:
            opened.append(os.open(directory, flags, dir_fd=opened[-1] if opened else root))
        info = os.stat(name, dir_fd=opened[-1], follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or _modified(info) >= timezone.now() - ORPHAN_MIN_AGE:
            return False
        os.unlink(name, dir_fd=opened[-1])
    except FileNotFoundError:
        return False
    except OSError:
        # A link or a plain file where a directory should be, or no permission.
        return False
    finally:
        for descriptor in opened:
            os.close(descriptor)
    return True


def delete_orphans(orphans: Sequence[Orphan], storage_root: Path) -> int:
    """Delete what is still a regular, old file of the layout; returns how many went.

    Refuses a `storage_root` that is not the configured one, not /data/documents (or in
    a safe temporary folder: `directory_not_allowed`), or overlaps the back-up mount,
    whoever calls it. The root is then opened component by component without following
    a link and checked to be the directory that was judged.
    """
    _judged_storage_root(storage_root)
    expected = safepaths.identity(storage_root)
    if expected is None:
        return 0
    root = safepaths.open_dir_nofollow(storage_root, expect=expected)
    try:
        return sum(1 for orphan in orphans if _unlink_if_old(root, orphan.path))
    finally:
        os.close(root)


def _judged_storage_root(storage_root: Path) -> Path:
    """The storage root when it is the configured one on the fixed list (or in the test
    root) and clear of the back-up mount; judged before it is listed or touched."""
    return safepaths.require_emptiable(
        storage_root,
        allowed=[safepaths.configured_path(settings.BRIEVIE_STORAGE_ROOT)],
        avoid=[safepaths.configured_path(settings.BRIEVIE_BACKUP_MOUNT)],
        fixed=[safepaths.DOCUMENTS],
    )


def sweep(*, delete: bool) -> SweepResult:
    # Judged before anything is listed: only an accepted root is ever read or touched.
    root = _judged_storage_root(safepaths.configured_path(settings.BRIEVIE_STORAGE_ROOT))
    with backup_lock():
        try:
            orphans = find_orphans(
                conninfo=backup_conninfo(dbname=str(connection.settings_dict["NAME"])),
                storage_root=root,
                now=timezone.now(),
            )
        except psycopg.Error:
            # The driver's message can quote the connection string; say only the code.
            raise BackupError("backup_role_missing") from None
        deleted = delete_orphans(orphans, root) if delete else 0
    return SweepResult(orphans=orphans, deleted=deleted)

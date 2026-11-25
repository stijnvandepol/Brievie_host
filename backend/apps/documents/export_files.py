"""Where export files live, and the guard every removal of one goes through.

An export file is `<storage root>/exports/<workspace id>/<export id>.sealed`, sealed
(see `apps.common.sealed_stream`), created with mode 0600 and never overwritten. The one
thing export code ever deletes is such a file:

- `judge_export_path` is a pure function (nothing is looked at, no file system call): it
  accepts exactly `<root>/<uuid>/<uuid>.sealed`, the root being `export_root()` and
  nothing else, which must lie inside a production directory or the test root; it
  refuses `/`, a home directory, a relative path, `..`, anything outside the export
  directory, a directory and any other name.
- `remove_export_file` calls it first, then refuses a link (the file, its directory or
  the export directory) and a path that resolves outside the root, and only then
  unlinks. A path from a database row is rebuilt by `export_file_path`, which accepts
  only the exact stored form.
"""

import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import UUID

from django.conf import settings

from apps.backup import safepaths
from apps.backup.errors import BackupError

EXPORT_DIRECTORY = "exports"
EXPORT_SUFFIX = ".sealed"
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_ONLY = re.compile(_UUID)
_FILE_NAME = re.compile(rf"{_UUID}\.sealed")
_STORED_PATH = re.compile(rf"{EXPORT_DIRECTORY}/{_UUID}/{_UUID}\.sealed")
# "/" and at least `<top>/<storage root>/exports`.
_MIN_ROOT_PARTS = 4
_OWNER_ONLY_DIRECTORY = 0o700
_OWNER_ONLY_FILE = 0o600


class UnsafePathError(Exception):
    """A path that is not an export file under the export directory. Never carries the
    path in its message: a path holds ids, and callers log the type only."""


def export_root() -> Path:
    return Path(settings.BRIEVIE_STORAGE_ROOT) / EXPORT_DIRECTORY


def _unsafe() -> UnsafePathError:
    return UnsafePathError("Not an export file.")


def judge_export_root(root: Path) -> Path:
    """`root` when it is an export directory that may be destroyed in: an absolute
    directory named `exports` inside a production directory or the installed test root
    (the mechanism of `apps.backup.safepaths`). Pure: decided by the text alone."""
    if "\0" in str(root) or ".." in root.parts or not root.is_absolute():
        raise _unsafe()
    if root.name != EXPORT_DIRECTORY or len(root.parts) < _MIN_ROOT_PARTS:
        raise _unsafe()
    try:
        safepaths.require_below_listed(root)
    except BackupError:
        raise _unsafe() from None
    return root


def judge_export_path(path: Path) -> Path:
    """`path` when it is `<export directory>/<uuid>/<uuid>.sealed`, else `UnsafePathError`.

    Pure: decided by the text of the path and of `export_root()` alone (no file system
    call). A path under another directory that happens to be named `exports` is refused,
    and so is `/`, a home directory, a relative path, `..`, NUL, a directory and any
    other name. The root is never a caller's choice.
    """
    root = judge_export_root(export_root())
    if "\0" in str(path) or ".." in path.parts or not path.is_absolute():
        raise _unsafe()
    workspace_directory = path.parent
    if (
        workspace_directory.parent != root
        or not _UUID_ONLY.fullmatch(workspace_directory.name)
        or not _FILE_NAME.fullmatch(path.name)
    ):
        raise _unsafe()
    return path


def export_file_path(storage_path: str) -> Path:
    """The file of a row's `storage_path` (relative to the storage root), guarded: only
    the exact form `exports/<uuid>/<uuid>.sealed` is accepted."""
    if "\\" in storage_path or not _STORED_PATH.fullmatch(storage_path):
        raise _unsafe()
    relative = PurePosixPath(storage_path)
    absolute = Path(settings.BRIEVIE_STORAGE_ROOT).joinpath(*relative.parts)
    return judge_export_path(absolute)


def _refuse_links(path: Path) -> None:
    """Not a link, not inside a linked directory, and resolving inside the root."""
    root = export_root()
    if path.is_symlink() or path.parent.is_symlink() or root.is_symlink():
        raise _unsafe()
    try:
        safepaths.require_inside(path, root)
    except BackupError:
        raise _unsafe() from None


def remove_export_file(path: Path) -> None:
    """Remove one export file; nothing else, and a file that is already gone is fine.

    The guard (`judge_export_path`) is the first step, so no caller can skip it. Never
    follows a link and never removes a directory.
    """
    judge_export_path(path)
    _refuse_links(path)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    if not stat.S_ISREG(mode):
        raise _unsafe()
    try:
        os.unlink(path)
    except FileNotFoundError:
        return


def open_export_for_write(path: Path) -> BinaryIO:
    """A new, empty, owner-only file for the export; never overwrites one (O_EXCL) and
    never follows a link. Its directory is made when needed."""
    judge_export_path(path)
    root = export_root()
    # The export directory and the workspace's, owner only (the parents' own mode is the
    # storage root's business).
    os.makedirs(root, mode=_OWNER_ONLY_DIRECTORY, exist_ok=True)
    os.makedirs(path.parent, mode=_OWNER_ONLY_DIRECTORY, exist_ok=True)
    _refuse_links(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, _OWNER_ONLY_FILE)
    return os.fdopen(descriptor, "wb")


def free_bytes() -> int:
    """Free bytes on the disk of the export directory (of the storage root while the
    directory does not exist yet). Looks only; makes and removes nothing."""
    root = judge_export_root(export_root())
    target = root if root.exists() else root.parent
    return shutil.disk_usage(target).free


def open_export_for_read(path: Path) -> BinaryIO:
    """The export file for reading; `FileNotFoundError` when it is gone."""
    judge_export_path(path)
    _refuse_links(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    return os.fdopen(descriptor, "rb")


@dataclass(frozen=True)
class ExportFile:
    path: Path
    workspace_id: UUID
    export_id: UUID
    modified: float


def list_export_files() -> list[ExportFile]:
    """The files that look like export files: `<export directory>/<uuid>/<uuid>.sealed`,
    regular files, found by listing the two levels below the export directory and no
    further. Links (of a directory or a file), other names and subdirectories are not
    listed and never followed."""
    root = judge_export_root(export_root())
    found: list[ExportFile] = []
    try:
        if root.is_symlink():
            return found
        with os.scandir(root) as top:
            directories = [
                e for e in top if _UUID_ONLY.fullmatch(e.name) and e.is_dir(follow_symlinks=False)
            ]
        for directory in directories:
            with os.scandir(directory.path) as inner:
                for entry in inner:
                    if not _FILE_NAME.fullmatch(entry.name):
                        continue
                    status = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(status.st_mode):
                        continue
                    found.append(
                        ExportFile(
                            path=Path(entry.path),
                            workspace_id=UUID(directory.name),
                            export_id=UUID(entry.name.removesuffix(EXPORT_SUFFIX)),
                            modified=status.st_mtime,
                        )
                    )
    except FileNotFoundError:
        return found
    return found

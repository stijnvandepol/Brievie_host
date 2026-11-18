"""Where export files live, and the guard every removal of one goes through.

An export file is `<storage root>/exports/<workspace id>/<export id>.sealed`, sealed
(see `apps.common.sealed_stream`), created with mode 0600 and never overwritten. The one
thing export code ever deletes is such a file:

- `judge_export_path` is a pure function of two paths (nothing is looked at, no file
  system call): it accepts exactly `<root>/<uuid>/<uuid>.sealed` below an absolute
  `.../exports` directory, and refuses `/`, a home directory, a relative path, `..`,
  anything outside the export directory, a directory and any other name.
- `remove_export_file` calls it first, then refuses a link (the file, its directory or
  the export directory) and a path that resolves outside the root, and only then
  unlinks. A path from a database row is rebuilt by `export_file_path`, which accepts
  only the exact stored form.
"""

import logging
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from django.conf import settings

from apps.backup import safepaths
from apps.backup.errors import BackupError

logger = logging.getLogger(__name__)

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


def judge_export_path(path: Path, root: Path) -> Path:
    """`path` when it is `<root>/<uuid>/<uuid>.sealed`, else `UnsafePathError`.

    Pure: decided by the text of the two paths alone. `root` must itself be an absolute
    directory named `exports`, below at least one more directory than `/` and a storage
    root, without `..`.
    """
    for candidate in (path, root):
        if "\0" in str(candidate) or ".." in candidate.parts or not candidate.is_absolute():
            raise _unsafe()
    if root.name != EXPORT_DIRECTORY or len(root.parts) < _MIN_ROOT_PARTS:
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
    return judge_export_path(absolute, export_root())


def _refuse_links(path: Path, root: Path) -> None:
    """Not a link, not inside a linked directory, and resolving inside the root."""
    if path.is_symlink() or path.parent.is_symlink() or root.is_symlink():
        raise _unsafe()
    try:
        safepaths.require_inside(path, root)
    except BackupError:
        raise _unsafe() from None


def remove_export_file(path: Path, root: Path | None = None) -> None:
    """Remove one export file; nothing else, and a file that is already gone is fine.

    The guard (`judge_export_path`) is the first step, so no caller can skip it. Never
    follows a link and never removes a directory.
    """
    root = root if root is not None else export_root()
    judge_export_path(path, root)
    _refuse_links(path, root)
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


def open_export_for_write(path: Path, root: Path | None = None) -> BinaryIO:
    """A new, empty, owner-only file for the export; never overwrites one (O_EXCL) and
    never follows a link. Its directory is made when needed."""
    root = root if root is not None else export_root()
    judge_export_path(path, root)
    # The export directory and the workspace's, owner only (the parents' own mode is the
    # storage root's business).
    os.makedirs(root, mode=_OWNER_ONLY_DIRECTORY, exist_ok=True)
    os.makedirs(path.parent, mode=_OWNER_ONLY_DIRECTORY, exist_ok=True)
    _refuse_links(path, root)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, _OWNER_ONLY_FILE)
    return os.fdopen(descriptor, "wb")


def open_export_for_read(path: Path, root: Path | None = None) -> BinaryIO:
    """The export file for reading; `FileNotFoundError` when it is gone."""
    root = root if root is not None else export_root()
    judge_export_path(path, root)
    _refuse_links(path, root)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    return os.fdopen(descriptor, "rb")

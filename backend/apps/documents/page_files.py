"""The only code that removes a letter's sealed page files, and the guard it goes through.

A letter's pages are `<storage root>/<workspace id>/<document id>/<n>.page` (see
`intake.page_path`), written through a `.tmp-*` file next to them. The purge removes
them before it deletes the rows; nothing else in the trash or the purge removes a file
(`DocumentStorage.delete` stays for the upload rollback only).

The refusal lives here, whoever calls:

- `judge_page_path` is pure (no file system call): it accepts exactly
  `<workspace id>/<document id>/<n>.page` for the ids of the call (lowercase, `n` ASCII
  digits) and refuses everything else: an absolute path, `..`, `\\`, NUL, another
  workspace's or letter's path, a directory, any other name.
- `judge_storage_root` accepts only the configured storage root when it is
  `/data/documents` (or strictly inside the test root, tests only), not a link, not
  reached through one, not a home directory or above one, and clear of the back-up
  mount (`apps.backup.safepaths.require_emptiable`, as the orphan sweep judges it).
- `remove_document_files` judges every path first (one refusal refuses the whole call
  before anything is looked at), then the root, then opens the root, the workspace
  directory and the letter's directory each without following a link, relative to its
  parent, and checks each is the directory it saw (same inode, same file system). It
  unlinks only regular files with a judged page name or a `.tmp-*` name, never a link
  and never a directory, and removes the letter's directory only when it is empty. A
  linked or swapped directory is refused.

`PageFileRefused` never carries a path or an id; callers log the type only.
"""

import errno
import logging
import os
import re
import stat
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from uuid import UUID

from django.conf import settings

from apps.backup import safepaths
from apps.backup.errors import BackupError

logger = logging.getLogger(__name__)

_TEMP_NAME = re.compile(r"\.tmp-[A-Za-z0-9_]+")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
# What opening a link (or a file) with O_DIRECTORY | O_NOFOLLOW answers.
_LINK_ERRORS = frozenset({errno.ELOOP, errno.ENOTDIR, errno.EMLINK})


class PageFileRefused(Exception):  # noqa: N818 (the name the plan gives the interface)
    """A path or a root that is not a page file of this letter in the storage root.
    Never carries the path: a path holds ids, and callers log the type only."""


def _refused() -> PageFileRefused:
    return PageFileRefused("Not a page file of this letter.")


def judge_page_path(storage_path: str, *, workspace_id: UUID, document_id: UUID) -> PurePosixPath:
    """`storage_path` when it is exactly `<workspace_id>/<document_id>/<n>.page`.

    Pure: decided by the text alone, nothing is looked at.
    """
    if "\\" in storage_path or "\0" in storage_path:
        raise _refused()
    expected = re.compile(
        rf"{re.escape(str(workspace_id))}/{re.escape(str(document_id))}/[0-9]+\.page",
        re.ASCII,
    )
    if expected.fullmatch(storage_path) is None:
        raise _refused()
    judged = PurePosixPath(storage_path)
    if judged.is_absolute() or ".." in judged.parts or len(judged.parts) != 3:
        raise _refused()
    return judged


def judge_storage_root(root: Path) -> Path:
    """`root` when it is the configured storage root and may be destroyed in.

    The configured root, the fixed production path (`/data/documents`) or a folder
    strictly inside the test root (tests only), not a link nor reached through one, not a
    home directory, `/` or anything above a home, and clear of the back-up mount.
    """
    try:
        return safepaths.require_emptiable(
            root,
            allowed=[safepaths.configured_path(settings.BRIEVIE_STORAGE_ROOT)],
            avoid=[safepaths.configured_path(settings.BRIEVIE_BACKUP_MOUNT)],
            fixed=[safepaths.DOCUMENTS],
        )
    except BackupError:
        raise _refused() from None


def _open_dir_at(parent: int, name: str) -> int | None:
    """A descriptor of real subdirectory `name` of `parent`; None when it is not there.

    Refused: a link, a file, or a directory that changed between the look and the open
    or lies on another file system (a mount inside the store is not ours to touch).
    """
    try:
        seen = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(seen.st_mode):
        raise _refused()
    try:
        child = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in _LINK_ERRORS:
            raise _refused() from None
        raise
    info = os.fstat(child)
    if (info.st_dev, info.st_ino) != (seen.st_dev, seen.st_ino) or (
        info.st_dev != os.fstat(parent).st_dev
    ):
        os.close(child)
        raise _refused()
    return child


def _unlink_regular(directory: int, name: str) -> bool:
    """Unlink `name` in `directory` when it is a regular file; never a link or a directory."""
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    try:
        os.unlink(name, dir_fd=directory)
    except FileNotFoundError:
        return False
    return True


def _remove_in_letter_dir(letter: int, page_names: set[str]) -> tuple[int, int]:
    """Unlink the judged pages and the `.tmp-*` leftovers; returns (removed, left)."""
    with os.scandir(letter) as entries:
        temps = {entry.name for entry in entries if _TEMP_NAME.fullmatch(entry.name)}
    removed = sum(1 for name in sorted(page_names | temps) if _unlink_regular(letter, name))
    with os.scandir(letter) as entries:
        left = sum(1 for _ in entries)
    return removed, left


def _remove_letter_dir(workspace: int, name: str) -> None:
    """Remove the (empty) letter directory; one that filled up meanwhile stays."""
    try:
        os.rmdir(name, dir_fd=workspace)
    except FileNotFoundError:
        return
    except OSError as error:
        if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
            raise


def remove_document_files(
    workspace_id: UUID, document_id: UUID, storage_paths: Sequence[str]
) -> int:
    """Remove the letter's page files and leftovers; returns how many files went.

    All or nothing on the paths: every one is judged before anything is looked at. Then
    the root is judged and opened without following a link. A file or directory that is
    already gone is fine.
    """
    page_names = {
        judge_page_path(path, workspace_id=workspace_id, document_id=document_id).name
        for path in storage_paths
    }
    try:
        configured = safepaths.configured_path(settings.BRIEVIE_STORAGE_ROOT)
    except BackupError:
        raise _refused() from None
    root_path = judge_storage_root(configured)
    expected = safepaths.identity(root_path)
    if expected is None:
        return 0
    try:
        root = safepaths.open_dir_nofollow(root_path, expect=expected)
    except BackupError:
        raise _refused() from None
    except FileNotFoundError:
        return 0
    try:
        workspace = _open_dir_at(root, str(workspace_id))
        if workspace is None:
            return 0
        try:
            letter = _open_dir_at(workspace, str(document_id))
            if letter is None:
                return 0
            try:
                removed, left = _remove_in_letter_dir(letter, page_names)
            finally:
                os.close(letter)
            if left:
                logger.warning(
                    "The folder of letter %s kept %d entries that are not its pages.",
                    document_id,
                    left,
                )
            else:
                _remove_letter_dir(workspace, str(document_id))
            return removed
        finally:
            os.close(workspace)
    finally:
        os.close(root)

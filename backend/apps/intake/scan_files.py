"""The only code that touches the scan folder's share (intake spec S 3.5, erase code).

Every function refuses on its own, in code, before it acts:

- the root is `settings.BRIEVIE_SCAN_ROOT` resolved once, never `/`, never equal to,
  inside or around the storage root, and only when `BRIEVIE_SCAN_DIR_SET`;
- a path is a tuple of names relative to the root, never a joined string: each name
  non-empty, not `.` or `..`, without `/` or NUL and not starting with `.`, at most two
  below the workspace folder;
- every directory is opened with `O_DIRECTORY | O_NOFOLLOW` relative to its parent's
  descriptor and every file with `O_NOFOLLOW`: a link at any level is refused, never
  followed; a file must be regular with one link;
- a file is removed only when the database says it was imported and it is still exactly
  the file that was imported; a refused file is moved within its workspace folder into
  `mislukt/` without ever overwriting; nothing in `mislukt/` is moved or removed;
- no directory is ever removed and nothing is recursive.

Refusals raise `ScanRefusedError(code)`; callers log the code only, never a name.
"""

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings

from apps.intake.scan_rules import RESERVED, Fingerprint, RelPath, is_candidate_name

if TYPE_CHECKING:
    from apps.intake.models import ScanFile

__all__ = [
    "Fingerprint",
    "ImportedFile",
    "ProbeResult",
    "RelPath",
    "ScanRefusedError",
    "claim_folder",
    "count_candidates",
    "ensure_folder",
    "list_candidates",
    "list_failed",
    "list_subfolders",
    "move_to_failed",
    "open_root",
    "probe",
    "read_candidate",
    "remove_imported",
    "valid_name",
]

# The workspace folder and at most two names below it.
_MAX_DEPTH = 3
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# O_NONBLOCK: opening a FIFO for reading must not wait for a writer.
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOCTTY
_NEW_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_FOLDER_MODE = 0o2770
# Files Brievie writes on the share are its own only (0600). Removing them needs rights on
# the folder, not on the file; on a NAS the folder's inherited ACL decides who reads the
# reason file, and the Scanmap page shows every reason too.
_PRIVATE_MODE = 0o600
_REASON_MODE = _PRIVATE_MODE
_PROBE_NAME = ".brievie-check"
_CLAIM_NAME = ".brievie-werkruimte"
_CLAIM_MAX = 200
# A name on most filesystems is at most 255 bytes; a moved file needs room for " (999)"
# and its reason file for ".reden.txt".
_NAME_MAX_BYTES = 255
_REASON_SUFFIX = ".reden.txt"
_MAX_NUMBER = 1000


class ScanRefusedError(Exception):
    """`code`: root_unset, root_missing, root_is_slash, root_overlaps_storage, bad_path,
    not_a_folder, not_a_file, linked, changed, too_large, not_imported,
    failed_untouchable, no_free_name or not_writable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ImportedFile:
    """A file the database recorded as imported: the only kind `remove_imported` takes."""

    path: RelPath
    fingerprint: Fingerprint

    @classmethod
    def from_row(cls, row: "ScanFile") -> "ImportedFile":
        if row.state != "imported" or row.document_id is None:
            raise ScanRefusedError("not_imported")
        return cls(
            path=tuple(row.relative_path.split("/")),
            fingerprint=Fingerprint(
                device=row.device, inode=row.inode, size=row.size, mtime_ns=row.mtime_ns
            ),
        )


@dataclass(frozen=True)
class ProbeResult:
    """ok, missing, not_readable or not_writable, and the folder's owner."""

    state: str
    owner_uid: int
    owner_gid: int


def valid_name(name: str) -> bool:
    """One name of a path: not empty, not `.` or `..`, no `/` or NUL, and valid UTF-8 (a
    name from an old scanner in another encoding cannot be stored; it is left alone)."""
    if not name or name in (".", "..") or "/" in name or "\x00" in name:
        return False
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _check(path: RelPath) -> None:
    if not 1 <= len(path) <= _MAX_DEPTH:
        raise ScanRefusedError("bad_path")
    for name in path:
        if not isinstance(name, str) or not valid_name(name) or name.startswith("."):
            raise ScanRefusedError("bad_path")


def _in_failed(path: RelPath) -> bool:
    return any(name.casefold() == RESERVED for name in path[1:])


def _overlaps(root: str, storage: str) -> bool:
    return (
        root == storage
        or root.startswith(storage.rstrip(os.sep) + os.sep)
        or storage.startswith(root.rstrip(os.sep) + os.sep)
    )


def open_root() -> int:
    """A descriptor of the scan root; the caller closes it."""
    if not settings.BRIEVIE_SCAN_DIR_SET:
        raise ScanRefusedError("root_unset")
    root = os.path.realpath(settings.BRIEVIE_SCAN_ROOT)
    if root == os.sep:
        raise ScanRefusedError("root_is_slash")
    if not os.path.isdir(root):
        raise ScanRefusedError("root_missing")
    if _overlaps(root, os.path.realpath(settings.BRIEVIE_STORAGE_ROOT)):
        raise ScanRefusedError("root_overlaps_storage")
    try:
        return os.open(root, _DIR_FLAGS)
    except OSError:
        raise ScanRefusedError("root_missing") from None


def _open_dir(parent: int, name: str) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        raise
    except OSError:
        # A link (ELOOP on Linux, ENOTDIR or EMLINK elsewhere), a file, or no rights.
        raise ScanRefusedError("not_a_folder") from None


@contextmanager
def _folder(root_fd: int, folder: RelPath) -> Iterator[int]:
    """A descriptor of `folder`, walked one name at a time without following links."""
    fds: list[int] = []
    try:
        current = root_fd
        for name in folder:
            try:
                current = _open_dir(current, name)
            except FileNotFoundError:
                raise ScanRefusedError("not_a_folder") from None
            fds.append(current)
        yield current
    finally:
        for fd in reversed(fds):
            os.close(fd)


def ensure_folder(root_fd: int, path: RelPath) -> None:
    """Make each missing folder of `path`, one level at a time; an existing one is fine
    (a link is refused). A folder Brievie makes is group-writable with the group bit set,
    so the scanner's account can write in it and new files keep the share's group."""
    _check(path)
    fds: list[int] = []
    try:
        current = root_fd
        for name in path:
            created = False
            try:
                os.mkdir(name, _FOLDER_MODE, dir_fd=current)
                created = True
            except FileExistsError:
                pass
            except OSError:
                raise ScanRefusedError("not_a_folder") from None
            try:
                current = _open_dir(current, name)
            except FileNotFoundError:
                raise ScanRefusedError("not_a_folder") from None
            fds.append(current)
            if created:
                # The mode given to mkdir is narrowed by the umask.
                os.fchmod(current, _FOLDER_MODE)
    finally:
        for fd in reversed(fds):
            os.close(fd)


_INT8_MIN = -(2**63)
_INT8_MAX = 2**63 - 1


def int8(value: int) -> int:
    """`value` as a signed 64-bit number (a PostgreSQL bigint): an unsigned 64-bit inode or
    device keeps its bits, an mtime past the year 2262 or before 1677 is clamped (security
    review of PR #64, M1: one odd file must not break every round)."""
    if _INT8_MIN <= value <= _INT8_MAX:
        return value
    if _INT8_MAX < value < 2**64:
        return value - 2**64
    return _INT8_MAX if value > 0 else _INT8_MIN


def _fingerprint(info: os.stat_result) -> Fingerprint:
    return Fingerprint(
        device=int8(info.st_dev),
        inode=int8(info.st_ino),
        size=int8(info.st_size),
        mtime_ns=int8(info.st_mtime_ns),
    )


def _regular(info: os.stat_result) -> bool:
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def list_candidates(
    root_fd: int, folder: RelPath, *, limit: int | None = None
) -> list[tuple[RelPath, Fingerprint]]:
    """The files the job may read directly in `folder`: regular, one link, a PDF, JPEG
    or PNG name, in name order, at most `limit`. Links, other kinds and deeper folders are
    left out; only candidate names are looked at (a folder flooded with other files costs a
    listing, no `stat` each)."""
    _check(folder)
    found: list[tuple[RelPath, Fingerprint]] = []
    with _folder(root_fd, folder) as fd:
        for name in sorted(os.listdir(fd)):
            if limit is not None and len(found) >= limit:
                break
            if not valid_name(name) or name.startswith(".") or not is_candidate_name(name):
                continue
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError:
                continue
            if _regular(info):
                found.append(((*folder, name), _fingerprint(info)))
    return found


def count_candidates(root_fd: int, folder: RelPath) -> int:
    """How many names in `folder` look like a candidate, without a `stat` each: a folder
    flooded with files costs one listing (security review of PR #64, M4). 0 when the
    folder cannot be read."""
    try:
        _check(folder)
        with _folder(root_fd, folder) as fd:
            return sum(
                1
                for name in os.listdir(fd)
                if valid_name(name) and not name.startswith(".") and is_candidate_name(name)
            )
    except (ScanRefusedError, OSError):
        return 0


def list_subfolders(root_fd: int, folder: RelPath) -> list[str]:
    """The directories directly in `folder` (no links), by name. The kind comes from the
    listing itself where the filesystem gives it, so a flooded folder needs no `stat` per
    file."""
    _check(folder)
    with _folder(root_fd, folder) as fd, os.scandir(fd) as entries:
        return sorted(
            entry.name
            for entry in entries
            if valid_name(entry.name) and not entry.name.startswith(".") and _is_folder(entry)
        )


def _is_folder(entry: "os.DirEntry[str]") -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _is_file(entry: "os.DirEntry[str]") -> bool:
    try:
        return entry.is_file(follow_symlinks=False)
    except OSError:
        return False


def read_candidate(root_fd: int, path: RelPath, expected: Fingerprint, *, max_bytes: int) -> bytes:
    """The bytes of the file, only when it is still the file the job looked at."""
    _check(path)
    if len(path) < 2:
        raise ScanRefusedError("bad_path")
    with _folder(root_fd, path[:-1]) as parent:
        try:
            fd = os.open(path[-1], _FILE_FLAGS, dir_fd=parent)
        except FileNotFoundError:
            raise ScanRefusedError("changed") from None
        except OSError:
            raise ScanRefusedError("not_a_file") from None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ScanRefusedError("not_a_file")
            if info.st_nlink != 1:
                raise ScanRefusedError("linked")
            if _fingerprint(info) != expected:
                raise ScanRefusedError("changed")
            if info.st_size > max_bytes:
                raise ScanRefusedError("too_large")
            chunks: list[bytes] = []
            left = info.st_size + 1
            while left > 0:
                chunk = os.read(fd, min(left, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                left -= len(chunk)
            data = b"".join(chunks)
            if len(data) != info.st_size or _fingerprint(os.fstat(fd)) != expected:
                raise ScanRefusedError("changed")
            return data
        finally:
            os.close(fd)


def remove_imported(root_fd: int, imported: ImportedFile) -> None:
    """Remove the source of an imported letter: only while it is exactly the file that was
    imported (device, inode, size, mtime), a regular file with one link."""
    path = imported.path
    _check(path)
    if len(path) < 2:
        raise ScanRefusedError("bad_path")
    if _in_failed(path):
        raise ScanRefusedError("failed_untouchable")
    with _folder(root_fd, path[:-1]) as parent:
        try:
            info = os.stat(path[-1], dir_fd=parent, follow_symlinks=False)
        except OSError:
            raise ScanRefusedError("changed") from None
        if not _regular(info) or _fingerprint(info) != imported.fingerprint:
            raise ScanRefusedError("changed")
        os.unlink(path[-1], dir_fd=parent)


def _shortened(stem: str, budget: int) -> str:
    """`stem` cut to at most `budget` bytes of UTF-8, on a character boundary."""
    return stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore")


def _numbered(name: str, number: int) -> str:
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        stem, extension = name, ""
    tail = (f" ({number})" if number > 1 else "") + (f".{extension}" if extension else "")
    budget = _NAME_MAX_BYTES - len(_REASON_SUFFIX) - len(" (999)") - len(extension) - 1
    return _shortened(stem, max(budget, 1)) + tail


def _remove_own(fd: int, name: str, inode: int) -> None:
    """Remove a placeholder this module just made, and only that one."""
    try:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except OSError:
        return
    if info.st_ino == inode and _regular(info):
        os.unlink(name, dir_fd=fd)


def move_to_failed(root_fd: int, path: RelPath, *, reason_text: str) -> RelPath:
    """Move a refused file into `<workspace folder>/mislukt/` with `<name>.reden.txt`
    beside it; a taken name gets " (2)", " (3)"... Nothing is ever overwritten: the target
    name is reserved first with an empty file made with O_EXCL, which the rename then
    replaces. Returns the new path."""
    _check(path)
    if len(path) < 2:
        raise ScanRefusedError("bad_path")
    if _in_failed(path):
        raise ScanRefusedError("failed_untouchable")
    target_folder: RelPath = (path[0], RESERVED)
    with _folder(root_fd, path[:-1]) as source:
        try:
            info = os.stat(path[-1], dir_fd=source, follow_symlinks=False)
        except FileNotFoundError:
            raise ScanRefusedError("changed") from None
        except OSError:
            raise ScanRefusedError("not_a_file") from None
        if not stat.S_ISREG(info.st_mode):
            raise ScanRefusedError("not_a_file")
        ensure_folder(root_fd, target_folder)
        with _folder(root_fd, target_folder) as target:
            for number in range(1, _MAX_NUMBER):
                name = _numbered(path[-1], number)
                reason = name + _REASON_SUFFIX
                try:
                    placeholder = os.open(name, _NEW_FILE_FLAGS, _PRIVATE_MODE, dir_fd=target)
                except FileExistsError:
                    continue
                except OSError:
                    raise ScanRefusedError("no_free_name") from None
                placeholder_inode = os.fstat(placeholder).st_ino
                os.close(placeholder)
                try:
                    reason_fd = os.open(reason, _NEW_FILE_FLAGS, _REASON_MODE, dir_fd=target)
                except FileExistsError:
                    _remove_own(target, name, placeholder_inode)
                    continue
                except OSError:
                    _remove_own(target, name, placeholder_inode)
                    raise ScanRefusedError("no_free_name") from None
                try:
                    reason_inode = os.fstat(reason_fd).st_ino
                    try:
                        os.rename(path[-1], name, src_dir_fd=source, dst_dir_fd=target)
                    except OSError:
                        _remove_own(target, name, placeholder_inode)
                        _remove_own(target, reason, reason_inode)
                        raise ScanRefusedError("changed") from None
                    os.write(reason_fd, (reason_text.strip() + "\n").encode("utf-8"))
                finally:
                    os.close(reason_fd)
                return (*target_folder, name)
    raise ScanRefusedError("no_free_name")


def list_failed(root_fd: int, folder: RelPath) -> set[str]:
    """The names in `<folder>/mislukt/` (read only)."""
    _check(folder)
    try:
        with _folder(root_fd, (*folder, RESERVED)) as fd, os.scandir(fd) as entries:
            return {
                entry.name
                for entry in entries
                if valid_name(entry.name) and not entry.name.startswith(".") and _is_file(entry)
            }
    except (ScanRefusedError, OSError):
        return set()


def claim_folder(root_fd: int, folder: RelPath, owner: str) -> str:
    """Make `folder` the workspace's own before anything is written in it: "ours", "taken"
    (another workspace claimed it) or "not_empty".

    A folder Brievie makes itself, or an existing one that is empty (a NAS's own `@eaDir`,
    `#recycle` or dot files aside), gets `.brievie-werkruimte` with `owner` (O_EXCL). An
    existing folder with anything else in it is never claimed: a workspace admin cannot
    point Brievie at another office's folder and take its files in (security review of PR
    #64, M3). A claim of another workspace, or a link in its place, is not ours; removing
    the file by hand gives the folder free."""
    _check(folder)
    expected = (owner + "\n").encode("utf-8")
    with _folder(root_fd, folder[:-1]) as parent:
        made = False
        try:
            os.mkdir(folder[-1], _FOLDER_MODE, dir_fd=parent)
            made = True
        except FileExistsError:
            pass
        except OSError:
            raise ScanRefusedError("not_a_folder") from None
        try:
            fd = _open_dir(parent, folder[-1])
        except FileNotFoundError:
            raise ScanRefusedError("not_a_folder") from None
    try:
        if made:
            os.fchmod(fd, _FOLDER_MODE)
        try:
            existing = os.open(_CLAIM_NAME, _FILE_FLAGS, dir_fd=fd)
        except FileNotFoundError:
            existing = None
        except OSError:
            return "taken"
        if existing is not None:
            try:
                info = os.fstat(existing)
                if not _regular(info) or info.st_size > _CLAIM_MAX:
                    return "taken"
                if info.st_size == 0 and info.st_uid == os.getuid():
                    # A round that died between making and writing its claim: finish it.
                    existing_write = os.open(_CLAIM_NAME, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        os.write(existing_write, expected)
                    finally:
                        os.close(existing_write)
                    return "ours"
                return "ours" if os.read(existing, _CLAIM_MAX + 1) == expected else "taken"
            finally:
                os.close(existing)
        if not made and any(not name.startswith((".", "@", "#")) for name in os.listdir(fd)):
            return "not_empty"
        try:
            claim = os.open(_CLAIM_NAME, _NEW_FILE_FLAGS, _PRIVATE_MODE, dir_fd=fd)
        except FileExistsError:
            return "taken"
        except OSError:
            # No right to write here: the probe says so.
            raise ScanRefusedError("not_writable") from None
        try:
            os.write(claim, expected)
        finally:
            os.close(claim)
        return "ours"
    finally:
        os.close(fd)


def probe(root_fd: int, folder: RelPath) -> ProbeResult:
    """What the `intake` service can do in `folder`, for the Scanmap page: read it, and make
    and remove a probe file `.brievie-check` (O_EXCL; removed only when it is the one it
    just made). With `claim_folder` the only functions that write a dot name; only this one
    removes one."""
    _check(folder)
    fds: list[int] = []
    try:
        current = root_fd
        for name in folder:
            try:
                current = os.open(name, _DIR_FLAGS, dir_fd=current)
            except FileNotFoundError:
                return ProbeResult("missing", 0, 0)
            except OSError:
                return ProbeResult("not_readable", 0, 0)
            fds.append(current)
        return _probe_in(current)
    finally:
        for fd in reversed(fds):
            os.close(fd)


def _probe_in(fd: int) -> ProbeResult:
    info = os.fstat(fd)
    owner = (info.st_uid, info.st_gid)
    try:
        os.listdir(fd)
    except OSError:
        return ProbeResult("not_readable", *owner)
    for attempt in range(2):
        try:
            check = os.open(_PROBE_NAME, _NEW_FILE_FLAGS, _PRIVATE_MODE, dir_fd=fd)
            break
        except FileExistsError:
            if attempt or not _remove_stale_probe(fd):
                return ProbeResult("not_writable", *owner)
        except OSError:
            return ProbeResult("not_writable", *owner)
    inode = os.fstat(check).st_ino
    os.close(check)
    try:
        _remove_own(fd, _PROBE_NAME, inode)
    except OSError:
        return ProbeResult("not_writable", *owner)
    return ProbeResult("ok", *owner)


def _remove_stale_probe(fd: int) -> bool:
    """A probe file left by a probe that was cut off: empty, regular and ours."""
    try:
        info = os.stat(_PROBE_NAME, dir_fd=fd, follow_symlinks=False)
    except OSError:
        return False
    if not _regular(info) or info.st_size != 0 or info.st_uid != os.getuid():
        return False
    try:
        _remove_own(fd, _PROBE_NAME, info.st_ino)
    except OSError:
        return False
    return True

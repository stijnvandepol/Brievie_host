"""Path guards for everything in the back-up app that deletes, empties or overwrites.

Each guard only judges a path: it may look at the file system (is this a link, where
does it resolve, which inode is it) but never changes it. It returns the path when
Brievie may destroy or overwrite there, and raises BackupError otherwise:
`directory_not_allowed` for a directory that is not on the fixed list, `unsafe_path`
for everything else. The destructive functions call these guards themselves, as their
first step, so no caller can skip them; a test of a refusal calls the guard, never
the destructive code.

What may be emptied or restored into does not come from the settings that name the
directory: it is a fixed list of the paths the containers use, plus, for tests only,
a path strictly inside the test root that the test suite installs explicitly.
"""

import errno
import os
import pwd
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from apps.backup.errors import BackupError

# The directories a restore may empty and restore into, as the containers mount them.
DOCUMENTS = Path("/data/documents")
AUTHORITY_COPY = Path("/data/authority")
BACKUP_CONFIG = Path("/data/backup-config")
PRODUCTION_DIRECTORIES = (DOCUMENTS, AUTHORITY_COPY, BACKUP_CONFIG)
# Caddy's local authority and its leaf certificates, as the admin container mounts them.
CADDY_AUTHORITY = Path("/caddy-data/caddy/pki/authorities/local")
CADDY_LEAF = Path("/caddy-data/caddy/certificates/local")
CADDY_AUTHORITY_TAIL = ("caddy", "pki", "authorities", "local")
# The back-up disk and Caddy's volume: never emptied, never an output folder.
BACKUP_MOUNT = Path("/backup")
CADDY_DATA = Path("/caddy-data")
# Everything a restore output folder must stay clear of.
PRODUCTION_VOLUMES = (*PRODUCTION_DIRECTORIES, BACKUP_MOUNT, CADDY_DATA)
_LEAF_DIR = "certificates"
# "/" plus at least two components: /data/documents may be emptied, /data never.
_MIN_PARTS = 3
# Where home directories live (compared case-insensitively: macOS does not care).
_HOME_TOPS = frozenset({"users", "home"})
_ROOT_HOMES = (("root",), ("var", "root"), ("private", "var", "root"))
_GLOB = frozenset("*?[")
_Identity = tuple[int, int]


def _unsafe() -> BackupError:
    return BackupError("unsafe_path")


def _resolve(path: Path) -> Path:
    """`path.resolve()`; a path that cannot even be looked at is refused (fail closed)."""
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        raise _unsafe() from None


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink()
    except (OSError, ValueError):
        return True  # cannot tell: treat it as a link


def configured_path(value: str | os.PathLike[str]) -> Path:
    """A path from the settings: absolute, or refused (an empty value is not `.`)."""
    text = os.fspath(value)
    if not text or "\0" in text or not os.path.isabs(text):
        raise _unsafe()
    return Path(text)


# --- the home directory ----------------------------------------------------------------


def _password_home() -> str | None:
    """The current user's home directory from the password database, if any."""
    try:
        return pwd.getpwuid(os.getuid()).pw_dir
    except (KeyError, OSError):
        return None


def _home_candidates() -> list[Path]:
    """Every home directory of this user ($HOME and the password database).

    A relative or empty value counts for nothing; neither does a home at `/` (a service
    user without a home), which the root rule covers already.
    """
    homes: list[Path] = []
    for value in (os.environ.get("HOME", ""), _password_home() or ""):
        if value and os.path.isabs(value):
            try:
                home = Path(value).resolve()
            except (OSError, RuntimeError):
                continue
            if len(home.parts) > 1:
                homes.append(home)
    return homes


def _identity(path: Path) -> _Identity | None:
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    return info.st_dev, info.st_ino


def _identities(paths: Iterable[Path]) -> set[_Identity]:
    return {found for path in paths if (found := _identity(path)) is not None}


def _home_identities() -> tuple[set[_Identity], set[_Identity]]:
    """The inodes of the home directories, and of every directory above one."""
    homes = _home_candidates()
    above = _identities(parent for home in homes for parent in home.parents)
    return _identities(homes), above


def _folded(path: Path) -> list[str]:
    return [part.casefold() for part in path.parts]


def _under_home_tree(path: Path) -> bool:
    """`/Users/…`, `/home/…` or root's home, in any spelling: never Brievie's to destroy."""
    parts = _folded(path)[1:]
    if parts[:1] and parts[0] in _HOME_TOPS:
        return True
    return any(tuple(parts[: len(home)]) == home for home in _ROOT_HOMES)


def _at_or_above_home_tree(path: Path) -> bool:
    """`/`, `/Users`, `/Users/<name>`, `/home/<name>`, root's home or above, any spelling."""
    parts = _folded(path)[1:]
    if not parts:
        return True
    if parts[0] in _HOME_TOPS and len(parts) <= 2:
        return True
    return any(tuple(parts) == home[: len(parts)] for home in _ROOT_HOMES)


def _at_or_above_home(path: Path) -> bool:
    resolved = _resolve(path)
    if _at_or_above_home_tree(path) or _at_or_above_home_tree(resolved):
        return True
    homes, above = _home_identities()
    return _identity(resolved) in homes | above


def _touches_home(path: Path) -> bool:
    """`path` is a home directory, lies inside one, or lies above one."""
    resolved = _resolve(path)
    if _under_home_tree(path) or _under_home_tree(resolved) or _at_or_above_home(path):
        return True
    homes, _ = _home_identities()
    return bool(_identities([resolved, *resolved.parents]) & homes)


# --- the test root --------------------------------------------------------------


def _at_or_under(path: Path, ancestor: Path) -> bool:
    return path == ancestor or ancestor in path.parents


def _overlaps(one: Path, other: Path) -> bool:
    return _at_or_under(one, other) or _at_or_under(other, one)


# The one exception to the fixed list, for the test suite only: a root installed
# explicitly by tests/conftest.py (each test's own tmp_path). Never read from the
# environment (TMPDIR) or a setting, so production accepts only the fixed list.
_test_root: Path | None = None
# "/" plus at least three components: /private/var/folders/… or /tmp/pytest-of-…/…
_MIN_TEST_ROOT_PARTS = 4


def allow_test_root(root: Path) -> None:
    """Accept paths strictly inside `root` as destroyable, for the test suite only.

    Refused (`unsafe_path`): a relative root, `/` or one shallower than three
    components below it, a home directory or anything at, inside or above one, and a
    root at or above (or inside) a production path.
    """
    global _test_root
    _plain_absolute(root)
    resolved = _resolve(root)
    if len(resolved.parts) < _MIN_TEST_ROOT_PARTS or _through_link(root):
        raise _unsafe()
    if _touches_home(root):
        raise _unsafe()
    if any(_overlaps(resolved, path) for path in PRODUCTION_VOLUMES):
        raise _unsafe()
    _test_root = resolved


def clear_test_root() -> None:
    global _test_root
    _test_root = None


def installed_test_root() -> Path | None:
    return _test_root


def _in_test_root(resolved: Path) -> bool:
    return _test_root is not None and _test_root in resolved.parents


# --- the structure of a path -----------------------------------------------------------


def _through_link(path: Path) -> bool:
    """Whether `path` or one of its parents is a link, other than a system link at the
    top level (macOS reaches its temporary folder through /var -> /private/var)."""
    return _is_link(path) or any(
        len(parent.parts) > 2 and _is_link(parent) for parent in path.parents
    )


def _plain_absolute(path: Path) -> None:
    text = str(path)
    if not path.is_absolute() or ".." in path.parts or "\0" in text:
        raise _unsafe()
    if _GLOB & set(text):
        raise _unsafe()


def _structure(path: Path) -> Path:
    """`path` resolved, after the rules every destroyable directory obeys."""
    _plain_absolute(path)
    if _under_home_tree(path) or _through_link(path):
        raise _unsafe()
    resolved = _resolve(path)
    if len(path.parts) < _MIN_PARTS or len(resolved.parts) < _MIN_PARTS:
        raise _unsafe()
    if _touches_home(path):
        raise _unsafe()
    return resolved


def _require_listed(resolved: Path, fixed: Sequence[Path]) -> None:
    if resolved not in set(fixed) and not _in_test_root(resolved):
        raise BackupError("directory_not_allowed")


def _avoiding(resolved: Path, avoid: Sequence[Path | str]) -> None:
    for other in avoid:
        if not os.path.isabs(os.fspath(other)):
            raise _unsafe()  # an empty or relative setting: never the current directory
        if _overlaps(resolved, _resolve(Path(other))):
            raise _unsafe()


def require_emptiable(
    directory: Path,
    *,
    allowed: Sequence[Path | str],
    avoid: Sequence[Path | str] = (),
    fixed: Sequence[Path] = PRODUCTION_DIRECTORIES,
) -> Path:
    """`directory` when its contents may be removed or restored; refuses anything else.

    Refused (`unsafe_path`): a relative path, one with `..` or a glob character; a path
    that is a link or is reached through one (except a system link directly below `/`);
    `/` or a single directory below it; a home directory (found by inode from $HOME and
    the password database), anything inside or above one, and anything under `/Users`,
    `/home` or root's home in any spelling. Refused (`directory_not_allowed`): anything
    not on `fixed` (the production directories) and not strictly inside the test
    root (tests only). Refused (`unsafe_path`) again: a directory that is not one of
    `allowed` (what the caller was configured to empty), or that is, contains or lies
    inside one of `avoid` (the back-up mount, the other directories).
    """
    resolved = _structure(directory)
    _require_listed(resolved, fixed)
    trusted = {_resolve(Path(path)) for path in allowed if os.path.isabs(os.fspath(path))}
    if resolved not in trusted:
        raise _unsafe()
    _avoiding(resolved, avoid)
    return directory


def require_inside(path: Path, root: Path) -> Path:
    """`path` when it resolves strictly inside `root`.

    `root` must be absolute (an empty setting is never the current directory), not `/`,
    and not a home directory or above one.
    """
    _plain_absolute(root)
    if _at_or_above_home(root):
        raise _unsafe()
    resolved_root = _resolve(root)
    if len(resolved_root.parts) < 2:
        raise _unsafe()
    if resolved_root not in _resolve(path).parents:
        raise _unsafe()
    return path


def require_caddy_authority_dir(target: Path) -> Path:
    """`target` when it is Caddy's `…/caddy/pki/authorities/local` and not reached
    through a link: the fixed production path, or one inside the test root."""
    _plain_absolute(target)
    if tuple(target.parts[-len(CADDY_AUTHORITY_TAIL) :]) != CADDY_AUTHORITY_TAIL:
        raise _unsafe()
    resolved = _structure(target)
    _require_listed(resolved, [CADDY_AUTHORITY])
    return target


def leaf_dir(target: Path) -> Path:
    """`<…>/caddy/certificates/local` for a valid authority directory `target`.

    The target is validated here as well, and neither the leaf directory nor its
    parent may be a link.
    """
    validated = require_caddy_authority_dir(target)
    leaf = validated.parents[2] / _LEAF_DIR / validated.name
    if _is_link(leaf.parent) or _is_link(leaf):
        raise _unsafe()
    return leaf


def require_out_dir(out_dir: Path, *, avoid: Sequence[Path | str]) -> Path:
    """`out_dir` when a restore may write its files there.

    Absolute, not `/`, not a home directory or above one, not reached through a link,
    and not one of `avoid` (the configured directories, the production list, the
    back-up mount), inside one or above one. Whether it is new or empty is checked
    when it is opened.
    """
    _plain_absolute(out_dir)
    if _through_link(out_dir) or _at_or_above_home(out_dir):
        raise _unsafe()
    resolved = _resolve(out_dir)
    if len(resolved.parts) < 2:
        raise _unsafe()
    _avoiding(resolved, avoid)
    return out_dir


def require_plain_name(name: str) -> str:
    """`name` when it is one plain file name: no separator, not `.` or `..`."""
    separators = {"/", "\0", os.sep, *([os.altsep] if os.altsep else [])}
    if name in {"", ".", ".."} or any(separator in name for separator in separators):
        raise _unsafe()
    return name


# --- working through directory descriptors ---------------------------------------------
#
# A guard judges a path; the destructive code then opens that path component by
# component with O_NOFOLLOW and checks that it opened what was judged. Everything
# below works relative to directory descriptors, so a directory swapped for a link
# after the check is never followed.

_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY
_NO_LINK_ERRORS = frozenset({errno.ELOOP, errno.ENOTDIR, errno.EMLINK})
_SPECIAL_BITS = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX


def identity(path: Path) -> _Identity | None:
    """`(st_dev, st_ino)` of `path`, or None when it does not exist."""
    return _identity(path)


def _same(info: os.stat_result, other: os.stat_result | _Identity | None) -> bool:
    if other is None:
        return True
    wanted = (other.st_dev, other.st_ino) if isinstance(other, os.stat_result) else other
    return (info.st_dev, info.st_ino) == wanted


def _open_child_dir(parent: int, name: str, *, follow: bool = False) -> int:
    flags = _DIRECTORY if follow else _DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent)
    except OSError as error:
        if error.errno in _NO_LINK_ERRORS:
            raise _unsafe() from None
        raise


def open_dir_nofollow(
    path: Path, *, expect: _Identity | None = None, create: int | None = None
) -> int:
    """A descriptor of directory `path`, opened component by component without
    following a link (except a system link directly below `/`).

    `expect`: the identity the guard saw; a different directory is refused. `create`:
    the mode to make missing components with (each one made relative to its parent).
    """
    _plain_absolute(path)
    descriptor = os.open("/", _DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            try:
                child = _open_child_dir(descriptor, part, follow=index == 0)
            except FileNotFoundError:
                if create is None:
                    raise
                os.mkdir(part, create, dir_fd=descriptor)
                child = _open_child_dir(descriptor, part)
            os.close(descriptor)
            descriptor = child
        if not _same(os.fstat(descriptor), expect):
            raise _unsafe()
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _open_subdir(parent: int, name: str, seen: os.stat_result) -> int:
    """Open subdirectory `name` that was seen as `seen`; never another one, never
    one on another file system (a mount inside the tree is not ours to touch)."""
    child = _open_child_dir(parent, name)
    info = os.fstat(child)
    if not _same(info, seen) or info.st_dev != os.fstat(parent).st_dev:
        os.close(child)
        raise _unsafe()
    return child


def remove_tree_at(parent: int, name: str) -> None:
    """Remove `name` in directory `parent`: a file or link is unlinked, a directory
    emptied through descriptors and removed. Links are never followed."""
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=parent)
        return
    child = _open_subdir(parent, name, info)
    try:
        empty_dir(child)
    finally:
        os.close(child)
    os.rmdir(name, dir_fd=parent)


def empty_dir(descriptor: int) -> None:
    """Remove everything in the directory open as `descriptor` (it stays itself)."""
    with os.scandir(descriptor) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        remove_tree_at(descriptor, name)


@dataclass(frozen=True)
class Cleaned:
    removed: int  # links, special files and files with more than one hard link


def open_subdirs(base: int, names: Sequence[str], *, create: int | None = None) -> int:
    """A descriptor of `base/names[0]/…`, each opened without following a link; with
    `create`, missing ones are made with that mode. Nothing above `base` is touched."""
    descriptor = os.dup(base)
    try:
        for name in names:
            require_plain_name(name)
            try:
                child = _open_child_dir(descriptor, name)
            except FileNotFoundError:
                if create is None:
                    raise
                os.mkdir(name, create, dir_fd=descriptor)
                child = _open_child_dir(descriptor, name)
            os.close(descriptor)
            descriptor = child
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def check_tree(descriptor: int) -> None:
    """Before a restore writes into the tree open as `descriptor`: refuse
    (`restore_target_unsafe`) any entry that is not a directory or a regular file with
    one name. A link, a special file or a hard link could make a write land elsewhere.
    Nothing is changed."""
    with os.scandir(descriptor) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = _open_subdir(descriptor, name, info)
            try:
                check_tree(child)
            finally:
                os.close(child)
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise BackupError("restore_target_unsafe")


def _open_regular(parent: int, name: str, seen: os.stat_result) -> int | None:
    """A read descriptor of regular file `name` seen as `seen`; None when it changed."""
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except OSError as error:
        if error.errno in _NO_LINK_ERRORS:
            return None
        raise
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or not _same(info, seen):
        os.close(descriptor)
        return None
    return descriptor


def _settle(descriptor: int, info: os.stat_result, owner: int | None) -> None:
    """Clear setuid, setgid and sticky bits, and hand the inode to `owner`."""
    if info.st_mode & _SPECIAL_BITS:
        os.fchmod(descriptor, stat.S_IMODE(info.st_mode) & ~_SPECIAL_BITS)
    if owner is not None:
        os.fchown(descriptor, owner, owner)


def clean_tree(descriptor: int, *, owner: int | None) -> Cleaned:
    """After a restore: keep only regular files and directories in the tree open as
    `descriptor`. Anything else (links, devices, fifos, sockets) is removed and
    counted, and so is a file with more than one hard link (its other names may lie
    elsewhere: it is never kept with a foreign owner); setuid, setgid and sticky bits
    are cleared; with `owner`, every directory and file is handed to that uid and gid.
    """
    removed = 0
    _settle(descriptor, os.fstat(descriptor), owner)
    with os.scandir(descriptor) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = _open_subdir(descriptor, name, info)
            try:
                inner = clean_tree(child, owner=owner)
            finally:
                os.close(child)
            removed += inner.removed
            continue
        opened = _open_regular(descriptor, name, info) if stat.S_ISREG(info.st_mode) else None
        if opened is not None and os.fstat(opened).st_nlink > 1:
            os.close(opened)
            opened = None
        if opened is None:
            os.unlink(name, dir_fd=descriptor)
            removed += 1
            continue
        try:
            _settle(opened, info, owner)
        finally:
            os.close(opened)
    return Cleaned(removed=removed)


def chown_tree(descriptor: int, uid: int) -> int:
    """Hand the tree open as `descriptor` to `uid` through descriptors; returns how
    many entries were skipped (links, special files, files with more than one hard
    link). Nothing is followed, nothing is removed."""
    skipped = 0
    os.fchown(descriptor, uid, uid)
    with os.scandir(descriptor) as entries:
        names = [entry.name for entry in entries]
    for name in names:
        info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = _open_subdir(descriptor, name, info)
            try:
                skipped += chown_tree(child, uid)
            finally:
                os.close(child)
            continue
        opened = _open_regular(descriptor, name, info) if stat.S_ISREG(info.st_mode) else None
        if opened is None or info.st_nlink > 1:
            skipped += 1
            if opened is not None:
                os.close(opened)
            continue
        try:
            os.fchown(opened, uid, uid)
        finally:
            os.close(opened)
    return skipped


def read_regular_at(parent: int, name: str, *, limit: int) -> bytes | None:
    """The bytes of regular file `name` in `parent`, opened without following a link;
    None when it does not exist. A link, a non-regular file or one larger than
    `limit` bytes is refused."""
    require_plain_name(name)
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in _NO_LINK_ERRORS:
            raise _unsafe() from None
        raise
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise _unsafe()
        data = os.read(descriptor, limit + 1)
        if len(data) > limit:
            raise _unsafe()
        return data
    finally:
        os.close(descriptor)


def write_new_at(parent: int, name: str, data: bytes, *, mode: int = 0o600) -> int:
    """Create `name` in `parent` (it must not exist; no link followed), write and sync
    `data`; returns the open descriptor (the caller closes it)."""
    require_plain_name(name)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(name, flags, mode, dir_fd=parent)
    try:
        os.fchmod(descriptor, mode)
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        os.unlink(name, dir_fd=parent)
        raise
    return descriptor

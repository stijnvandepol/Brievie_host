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
a path strictly inside a temporary folder that is itself proven safe.
"""

import os
import pwd
import tempfile
from collections.abc import Iterable, Sequence
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
_LEAF_DIR = "certificates"
# "/" plus at least two components: /data/documents may be emptied, /data never.
_MIN_PARTS = 3
# Where home directories live (compared case-insensitively: macOS does not care).
_HOME_TOPS = frozenset({"users", "home"})
_ROOT_HOMES = (("root",), ("var", "root"), ("private", "var", "root"))
# Well-known temporary folders that are acceptable although short.
_SYSTEM_TEMPS = frozenset({Path("/tmp"), Path("/var/tmp"), Path("/private/tmp")})  # noqa: S108
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


# --- the temporary folder --------------------------------------------------------------


def _at_or_under(path: Path, ancestor: Path) -> bool:
    return path == ancestor or ancestor in path.parents


def _overlaps(one: Path, other: Path) -> bool:
    return _at_or_under(one, other) or _at_or_under(other, one)


def _safe_temp_root() -> Path | None:
    """The system's temporary folder, resolved, when it is safe to trust for tests.

    Not `/`, not relative, not a home directory, inside one or above one, not at or
    above a production directory, and at least three components long unless it is a
    well-known system temporary folder (`/tmp`).
    """
    raw = tempfile.gettempdir()
    if not raw or not os.path.isabs(raw):
        return None
    try:
        root = Path(raw).resolve()
        if _touches_home(root):
            return None
    except (BackupError, OSError, RuntimeError):
        return None
    if root not in _SYSTEM_TEMPS and len(root.parts) < _MIN_PARTS:
        return None
    production = (*PRODUCTION_DIRECTORIES, CADDY_AUTHORITY, CADDY_LEAF)
    if any(_at_or_under(directory, root) for directory in production):
        return None
    return root


def _in_safe_temp(resolved: Path) -> bool:
    root = _safe_temp_root()
    return root is not None and root in resolved.parents


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
    if resolved not in set(fixed) and not _in_safe_temp(resolved):
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
    not on `fixed` (the production directories) and not strictly inside a safe
    temporary folder. Refused (`unsafe_path`) again: a directory that is not one of
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
    through a link: the fixed production path, or one in a safe temporary folder."""
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

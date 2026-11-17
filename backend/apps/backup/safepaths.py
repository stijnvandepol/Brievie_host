"""Path guards for everything in the back-up app that deletes, empties or overwrites.

Each guard only judges a path: it may look at the file system (is this a link, where
does it resolve) but never changes it. It returns the path when Brievie may destroy
or overwrite it there, and raises BackupError("unsafe_path") otherwise. The
destructive functions call these guards themselves, as their first step, so no caller
can skip them; a test of a refusal calls the guard, never the destructive code.
"""

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from apps.backup.errors import BackupError

# The last four components of the directory where Caddy keeps its local authority.
CADDY_AUTHORITY_TAIL = ("caddy", "pki", "authorities", "local")
# Caddy issues its leaf certificates in `<caddy>/certificates/<name>`.
_LEAF_DIR = "certificates"
# "/" plus at least two components: /data/documents may be emptied, /data never.
_MIN_PARTS = 3


def _unsafe() -> BackupError:
    return BackupError("unsafe_path")


def _home() -> Path | None:
    """The current user's home directory, resolved; None when there is none."""
    try:
        home = Path.home()
    except (RuntimeError, KeyError):
        return None
    return home.resolve() if home.is_absolute() else None


def _temp_root() -> Path:
    """The system's temporary folder, resolved (on macOS through /var -> /private/var)."""
    return Path(tempfile.gettempdir()).resolve()


def _at_or_under(path: Path, ancestor: Path) -> bool:
    return path == ancestor or ancestor in path.parents


def _through_link(path: Path) -> bool:
    """Whether `path` or one of its parents is a link, other than a system link at the
    top level (macOS reaches its temporary folder through /var -> /private/var)."""
    return path.is_symlink() or any(
        len(parent.parts) > 2 and parent.is_symlink() for parent in path.parents
    )


def require_emptiable(directory: Path, *, allowed: Sequence[Path | str]) -> Path:
    """`directory` when its contents may be removed; refuses anything else.

    Refused: a relative path or one with `..`; a path that is a link or is reached
    through one (except a system link directly below `/`); a path that is, or
    resolves to, `/` or a single directory below it (`/data`, `/Users`); the home
    directory or any of its ancestors; inside the home directory anything that is not
    under the system's temporary folder; and every path that is not one of `allowed`
    (compared resolved, so a system link such as macOS's /var counts the same on
    both sides).
    """
    if not directory.is_absolute() or ".." in directory.parts:
        raise _unsafe()
    if _through_link(directory):
        raise _unsafe()
    resolved = directory.resolve()
    if len(directory.parts) < _MIN_PARTS or len(resolved.parts) < _MIN_PARTS:
        raise _unsafe()
    home = _home()
    if home is not None and len(home.parts) > 1:
        if _at_or_under(home, resolved):
            raise _unsafe()  # the home directory itself or one of its ancestors
        if home in resolved.parents and _temp_root() not in resolved.parents:
            raise _unsafe()  # a real folder in a home directory
    trusted = {Path(path).resolve() for path in allowed if Path(path).is_absolute()}
    if resolved not in trusted:
        raise _unsafe()
    return directory


def require_inside(path: Path, root: Path) -> Path:
    """`path` when it resolves strictly inside `root` (never `root` itself, never `/`)."""
    resolved_root = root.resolve()
    if len(resolved_root.parts) < 2:
        raise _unsafe()
    if resolved_root not in path.resolve().parents:
        raise _unsafe()
    return path


def require_caddy_authority_dir(target: Path) -> Path:
    """`target` when it is an absolute `<…>/caddy/pki/authorities/local` that is not
    reached through a link (except a system link directly below `/`)."""
    if not target.is_absolute() or ".." in target.parts:
        raise _unsafe()
    if tuple(target.parts[-len(CADDY_AUTHORITY_TAIL) :]) != CADDY_AUTHORITY_TAIL:
        raise _unsafe()
    if _through_link(target):
        raise _unsafe()
    return target


def leaf_dir(target: Path) -> Path:
    """`<…>/caddy/certificates/local` for a valid authority directory `target`.

    The target is validated here as well, and neither the leaf directory nor its
    parent may be a link.
    """
    validated = require_caddy_authority_dir(target)
    leaf = validated.parents[2] / _LEAF_DIR / validated.name
    if leaf.parent.is_symlink() or leaf.is_symlink():
        raise _unsafe()
    return leaf


def require_plain_name(name: str) -> str:
    """`name` when it is one plain file name: no separator, not `.` or `..`."""
    separators = {"/", "\0", os.sep, *([os.altsep] if os.altsep else [])}
    if name in {"", ".", ".."} or any(separator in name for separator in separators):
        raise _unsafe()
    return name

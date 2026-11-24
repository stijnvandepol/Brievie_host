"""`BRIEVIE_BACKUP_AUTO_INIT`: `init` prepares the back-up folder once, when that is safe.

Safe means all of: the setting is on, the destination is a folder (not a remote
repository), the folder exists and is empty (no marker, no other file), and `init` is
not given the same-disk override (the disk check of `destination.initialise` stays on).
A folder that already holds anything is left to the person who set it up.
"""

import os
from pathlib import Path


def folder_is_empty(mount: Path) -> bool:
    """True for an existing directory without a single entry (a link is not empty)."""
    try:
        if not mount.is_dir() or mount.is_symlink():
            return False
        with os.scandir(mount) as entries:
            return next(entries, None) is None
    except OSError:
        return False


def should_auto_init(*, enabled: bool, kind: str, mount: Path) -> bool:
    return enabled and kind == "folder" and folder_is_empty(mount)

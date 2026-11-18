import contextlib
import os
import secrets
from pathlib import Path

from apps.backup import safepaths


def write_private(directory: Path, name: str, text: str) -> None:
    """Write `directory/name` atomically, UTF-8, mode 0600, in a 0700 directory.

    Only into the back-up config folder (/data/backup-config, or one in a safe temporary
    folder), and only a plain file name (`unsafe_path` / `directory_not_allowed`
    otherwise). The folder is opened component by component without following a link
    (missing parts are made 0700); a temporary file is created next to the target,
    synced and renamed over it relative to the folder's descriptor, so a reader sees the
    old file or the new one, never half of one, and no link is ever written through.
    """
    safepaths.require_plain_name(name)
    safepaths.require_emptiable(directory, allowed=[directory], fixed=[safepaths.BACKUP_CONFIG])
    descriptor = safepaths.open_dir_nofollow(directory, create=0o700)
    temporary = f".{name}-{secrets.token_hex(8)}"
    try:
        handle = safepaths.write_new_at(descriptor, temporary, text.encode("utf-8"))
        os.close(handle)
        try:
            os.replace(temporary, name, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=descriptor)
            raise
    finally:
        os.close(descriptor)

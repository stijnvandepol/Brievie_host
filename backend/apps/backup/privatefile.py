import os
import tempfile
from pathlib import Path

from apps.backup.safepaths import require_plain_name


def write_private(directory: Path, name: str, text: str) -> None:
    """Write `directory/name` atomically, UTF-8, mode 0600, in a 0700 directory.

    A temporary file next to the target is synced and then renamed over it, so a
    reader sees the old file or the new one, never half of one. `name` is one plain
    file name (`unsafe_path` otherwise): nothing outside `directory` is ever written.
    """
    require_plain_name(name)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=f".{name}-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / name)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise

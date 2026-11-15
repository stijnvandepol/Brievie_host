"""Where document files live. Behind an interface: a disk now, S3-compatible later."""

import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Protocol

from django.conf import settings


class DocumentStorage(Protocol):
    def write(self, path: str, data: bytes) -> None: ...
    def read(self, path: str) -> bytes: ...
    def delete(self, path: str) -> None: ...


class FileSystemStorage:
    """Files under one root directory. Callers pass relative paths with `/`."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, path: str, data: bytes) -> None:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write next to the target and rename: a crash never leaves half a file.
        fd, temp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def read(self, path: str) -> bytes:
        return self._resolve(path).read_bytes()

    def delete(self, path: str) -> None:
        self._resolve(path).unlink(missing_ok=True)

    def _resolve(self, path: str) -> Path:
        parts = PurePosixPath(path).parts
        if not parts or path.startswith("/") or any(part in ("..", ".") for part in parts):
            raise ValueError(f"Not a storage path: {path!r}")
        return self.root.joinpath(*parts)


def default_storage() -> DocumentStorage:
    return FileSystemStorage(Path(settings.BRIEVIE_STORAGE_ROOT))

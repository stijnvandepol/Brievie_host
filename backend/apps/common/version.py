"""The server's one version number: `version` in backend/pyproject.toml.

The image copies backend/ to /app, so the running server reads the same file the
release was tagged from. The health endpoint, the back-up manifest and the update
all ask here; nothing else holds a version of the server.
"""

import functools
import tomllib
from pathlib import Path

from django.conf import settings


@functools.cache
def server_version() -> str:
    with (Path(settings.BASE_DIR) / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])

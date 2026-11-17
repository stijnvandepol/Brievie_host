"""The copy of deploy/.env that goes into the back-up, without the secrets."""

import os
import re
import tempfile
from pathlib import Path

from django.conf import settings

from apps.backup.errors import BackupError

# The master key and the database superuser/owner passwords never enter a back-up.
EXCLUDED_KEYS = (
    "BRIEVIE_MASTER_KEY",
    "POSTGRES_PASSWORD",
    "BRIEVIE_OWNER_PASSWORD",
    "DATABASE_OWNER_URL",
)
ENV_COPY_NAME = "env"

_SECRET_LINE = re.compile(
    r"^\s*(?:export\s+)?(?:" + "|".join(map(re.escape, EXCLUDED_KEYS)) + r")\s*=",
)


def store_env_copy(text: str) -> None:
    """Write the copy atomically with mode 0600; refuse text that holds an excluded key.

    The error carries only a code: never the offending line or its value.
    """
    if any(_SECRET_LINE.match(line) for line in text.splitlines()):
        raise BackupError("env_copy_has_secret")
    directory = Path(settings.BRIEVIE_BACKUP_CONFIG_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".env-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        os.replace(temporary, directory / ENV_COPY_NAME)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise

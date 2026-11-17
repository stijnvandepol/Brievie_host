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

# One line of a .env file as a parser may read it: an optional BOM, an optional
# comment marker, `export`, a quoted key, and `=` or `:`. Deliberately wider than
# any one parser: refusing too much only costs a refusal.
_KEY_LINE = re.compile(
    r"^[\s\ufeff]*(?P<comment>#[\s\ufeff#]*)?(?:export\s+)?[\"']?"
    r"(?:" + "|".join(map(re.escape, EXCLUDED_KEYS)) + r")[\"']?\s*[=:](?P<value>.*)$",
    re.IGNORECASE,
)


def _leaks(line: str) -> bool:
    match = _KEY_LINE.match(line)
    if match is None:
        return False
    if match["comment"] is None:
        return True
    # A commented-out key is a leak only when it still carries a value.
    return match["value"].strip().strip("\"'").strip() != ""


def store_env_copy(text: str) -> None:
    """Write the copy atomically with mode 0600; refuse text that holds an excluded key.

    The error carries only a code: never the offending line or its value.
    """
    if any(_leaks(line) for line in text.splitlines()):
        raise BackupError("env_copy_has_secret")
    directory = Path(settings.BRIEVIE_BACKUP_CONFIG_DIR)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".env-")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / ENV_COPY_NAME)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise

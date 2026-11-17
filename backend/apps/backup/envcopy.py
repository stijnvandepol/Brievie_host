"""The copy of deploy/.env that goes into the back-up, without the secrets, and back."""

import re
import secrets
from pathlib import Path

from django.conf import settings

from apps.backup.errors import BackupError
from apps.backup.privatefile import write_private

# The master key and the database superuser/owner passwords never enter a back-up.
EXCLUDED_KEYS = (
    "BRIEVIE_MASTER_KEY",
    "POSTGRES_PASSWORD",
    "BRIEVIE_OWNER_PASSWORD",
    "DATABASE_OWNER_URL",
)
ENV_COPY_NAME = "env"
DEFAULT_POSTGRES_PORT = "5432"


def _key_line(keys: tuple[str, ...]) -> re.Pattern[str]:
    """One line of a .env file that sets one of `keys`, as a parser may read it.

    An optional BOM, an optional comment marker, `export`, a quoted key, and `=` or
    `:`. Deliberately wider than any one parser: refusing too much only costs a refusal.
    """
    return re.compile(
        r"^[\s﻿]*(?P<comment>#[\s﻿#]*)?(?:export\s+)?[\"']?"
        r"(?P<key>" + "|".join(map(re.escape, keys)) + r")[\"']?\s*[=:](?P<value>.*)$",
        re.IGNORECASE,
    )


_KEY_LINE = _key_line(EXCLUDED_KEYS)


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
    write_private(Path(settings.BRIEVIE_BACKUP_CONFIG_DIR), ENV_COPY_NAME, text)


# Set by the restore in the line that already holds them, or added at the end.
_BACKUP_PASSWORD = "BRIEVIE_BACKUP_PASSWORD"  # noqa: S105 - a key name
_BACKUP_DIR = "BRIEVIE_BACKUP_DIR"
_REPLACED_LINE = _key_line((_BACKUP_PASSWORD, _BACKUP_DIR))
_PORT_LINE = _key_line(("POSTGRES_PORT",))
_PLAIN_VALUE = re.compile(r"[A-Za-z0-9._~+/=@:,-]*")
_UNQUOTABLE = re.compile(r"['\x00-\x1f\x7f]")
_RESTORED_HEADER = "# Added by the restore: the master key and new database passwords."


def _env_value(value: str) -> str:
    """A value as docker compose and `sh` read it literally: plain, or in single quotes."""
    if _PLAIN_VALUE.fullmatch(value):
        return value
    if _UNQUOTABLE.search(value):
        raise BackupError("backup_password_unwritable")
    return f"'{value}'"


def _postgres_port(lines: list[str]) -> str:
    port = DEFAULT_POSTGRES_PORT
    for line in lines:
        match = _PORT_LINE.match(line)
        if match is not None and match["comment"] is None:
            value = match["value"].strip().strip("\"'").strip()
            port = value if value.isdigit() else DEFAULT_POSTGRES_PORT
    return port


def restored_env(
    text: str, *, master_key_hex: str, backup_password: str, backup_dir_host: str | None
) -> str:
    """deploy/.env for a server restored from a back-up whose copy of it is `text`.

    Keeps every line and comment in order, except that an excluded key in any spelling
    is dropped and the back-up password (and the back-up folder, when given) is set in
    the line that holds it. Adds the master key and new superuser and owner passwords
    (hex, as init-env.sh makes them): the dump holds no password, so the new database
    takes these. `backup_password` is what opened the repository, so the keeper, which
    has no master key, can make back-ups after the restore.
    """
    lines = text.splitlines()
    owner_password = secrets.token_hex(24)
    wanted = {_BACKUP_PASSWORD: _env_value(backup_password)}
    if backup_dir_host is not None:
        wanted[_BACKUP_DIR] = _env_value(backup_dir_host)
    port = _postgres_port(lines)

    restored: list[str] = []
    written: set[str] = set()
    for line in lines:
        if _leaks(line):
            continue
        match = _REPLACED_LINE.match(line)
        if match is not None and match["comment"] is None:
            key = match["key"].upper()
            if key in wanted:
                if key not in written:
                    restored.append(f"{key}={wanted[key]}")
                    written.add(key)
                continue
        restored.append(line)

    restored.append(_RESTORED_HEADER)
    restored += [
        f"BRIEVIE_MASTER_KEY={master_key_hex}",
        f"POSTGRES_PASSWORD={secrets.token_hex(24)}",
        f"BRIEVIE_OWNER_PASSWORD={owner_password}",
        f"DATABASE_OWNER_URL=postgres://brievie_owner:{owner_password}@localhost:{port}/brievie",
    ]
    restored += [f"{key}={value}" for key, value in wanted.items() if key not in written]
    return "\n".join(restored) + "\n"

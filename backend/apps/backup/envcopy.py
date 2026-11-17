"""The copy of deploy/.env that goes into the back-up, without the secrets, and back."""

import re
import secrets
from dataclasses import dataclass
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


def _key_line(keys: tuple[str, ...], *, pattern: str | None = None) -> re.Pattern[str]:
    """One line of a .env file that sets one of `keys` (or a key matching `pattern`),
    as a parser may read it.

    An optional BOM, an optional comment marker, `export`, a quoted key, and `=` or
    `:`. Deliberately wider than any one parser: refusing too much only costs a refusal.
    """
    alternatives = pattern if pattern is not None else "|".join(map(re.escape, keys))
    return re.compile(
        r"^[\s﻿]*(?P<comment>#[\s﻿#]*)?(?:export\s+)?[\"']?"
        r"(?P<key>" + alternatives + r")[\"']?\s*[=:](?P<value>.*)$",
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
_REPLACED_LINE = _key_line((_BACKUP_PASSWORD,))
# Lines that decide which host folders, images, project and ports the containers get.
# They are never taken from a back-up: the installation that restores sets them
# (./brievie, from its own deploy/.env); BRIEVIE_BACKUP_DIR only when given explicitly.
HOST_KEYS = (
    _BACKUP_DIR,
    "BRIEVIE_BACKUP_REPOSITORY",
    "BRIEVIE_BACKUP_REST_USERNAME",
    "BRIEVIE_BACKUP_REST_PASSWORD",
    "BRIEVIE_BACKUP_S3_ACCESS_KEY_ID",
    "BRIEVIE_BACKUP_S3_SECRET_ACCESS_KEY",
    "BRIEVIE_IMAGE",
    "COMPOSE_PROJECT_NAME",
    "POSTGRES_PORT",
    "BRIEVIE_HTTP_PORT",
    "BRIEVIE_HTTPS_PORT",
)
_HOST_LINE = _key_line(HOST_KEYS)
# Every COMPOSE_* and DOCKER_* key steers docker compose itself (profiles, files, the
# daemon): never taken from a back-up either.
_TOOL_LINE = _key_line((), pattern=r"(?:COMPOSE|DOCKER)_[A-Za-z0-9_]+")
_PORT_LINE = _key_line(("POSTGRES_PORT",))
_PLAIN_VALUE = re.compile(r"[A-Za-z0-9._~+/=@:,-]*")
_UNQUOTABLE = re.compile(r"['\x00-\x1f\x7f]")
_INLINE_COMMENT = re.compile(r"\s+#.*$")
_MAX_PORT = 65535
_RESTORED_HEADER = "# Added by the restore: the master key and new database passwords."


def _env_value(value: str, refusal: str) -> str:
    """A value as docker compose and `sh` read it literally: plain, or in single quotes.

    `refusal` is the code when neither is possible (a quote or a control character).
    """
    if _PLAIN_VALUE.fullmatch(value):
        return value
    if _UNQUOTABLE.search(value):
        raise BackupError(refusal)
    return f"'{value}'"


def _port_value(raw: str) -> str:
    """The port of a `POSTGRES_PORT` value: quotes, spaces and an inline comment removed."""
    value = _INLINE_COMMENT.sub("", raw).strip().strip("\"'").strip()
    if not value.isdigit() or not 1 <= int(value) <= _MAX_PORT:
        raise BackupError("restore_failed")
    return str(int(value))


def _postgres_port(lines: list[str]) -> str:
    """The last `POSTGRES_PORT`; the default only when there is none."""
    port = DEFAULT_POSTGRES_PORT
    for line in lines:
        match = _PORT_LINE.match(line)
        if match is not None and match["comment"] is None:
            port = _port_value(match["value"])
    return port


@dataclass(frozen=True)
class RestoredEnv:
    text: str
    dropped: tuple[str, ...]  # the host keys of the back-up that were left out


def restored_env(
    text: str, *, master_key_hex: str, backup_password: str, backup_dir_host: str | None
) -> RestoredEnv:
    """deploy/.env for a server restored from a back-up whose copy of it is `text`.

    Keeps every line and comment in order, except that an excluded key in any spelling
    is dropped, the host keys (`HOST_KEYS`: folders, repository, image, project, ports)
    and every `COMPOSE_*` and `DOCKER_*` key are dropped and listed in `dropped`, and
    the back-up password is set in the line that holds it. The back-up folder is
    written only when `backup_dir_host` is given.
    Adds the master key and new superuser and owner passwords (hex, as init-env.sh makes
    them): the dump holds no password, so the new database takes these.
    `backup_password` is what opened the repository, so the keeper, which has no master
    key, can make back-ups after the restore.
    """
    lines = text.splitlines()
    owner_password = secrets.token_hex(24)
    wanted = {_BACKUP_PASSWORD: _env_value(backup_password, "backup_password_unwritable")}
    if backup_dir_host is not None:
        wanted[_BACKUP_DIR] = _env_value(backup_dir_host, "backup_folder_unwritable")
    port = _postgres_port(lines)

    restored: list[str] = []
    written: set[str] = set()
    dropped: list[str] = []
    for line in lines:
        if _leaks(line):
            continue
        host = _HOST_LINE.match(line) or _TOOL_LINE.match(line)
        if host is not None and host["comment"] is None:
            key = host["key"].upper()
            if key not in wanted and key not in dropped:
                dropped.append(key)
            continue
        match = _REPLACED_LINE.match(line)
        if match is not None and match["comment"] is None:
            key = match["key"].upper()
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
    return RestoredEnv(text="\n".join(restored) + "\n", dropped=tuple(dropped))

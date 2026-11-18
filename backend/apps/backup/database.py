import re
import subprocess

import psycopg
from django.conf import settings
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from apps.backup.errors import BackupError

_CONNECT_TIMEOUT = 10
_PG_DUMP_VERSION = re.compile(r"\(PostgreSQL\)\s+(\d+)")


def _conninfo(config: dict[str, object] | None, missing: str, dbname: str | None) -> str:
    if config is None:
        raise BackupError(missing)
    return make_conninfo(
        dbname=dbname or str(config["NAME"]),
        user=str(config["USER"]),
        password=str(config["PASSWORD"]),
        host=str(config["HOST"]),
        port=str(config["PORT"]),
        connect_timeout=str(_CONNECT_TIMEOUT),
    )


def backup_conninfo(*, dbname: str | None = None) -> str:
    return _conninfo(settings.DATABASE_BACKUP, "backup_role_missing", dbname)


def superuser_conninfo(*, dbname: str | None = None) -> str:
    return _conninfo(settings.DATABASE_SUPERUSER, "restore_failed", dbname)


def libpq_env(conninfo: str) -> dict[str, str]:
    """Environment for a child process (pg_dump, psql): the password never goes in argv."""
    parts = conninfo_to_dict(conninfo)
    mapping = {
        "PGHOST": "host",
        "PGPORT": "port",
        "PGUSER": "user",
        "PGPASSWORD": "password",
        "PGDATABASE": "dbname",
    }
    return {name: str(parts[key]) for name, key in mapping.items() if parts.get(key) is not None}


def require_backup_role(conninfo: str) -> None:
    """The connection must be a role that reads every row (BYPASSRLS), or a back-up is partial."""
    try:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            row = conn.execute(
                "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = current_user"
            ).fetchone()
    except psycopg.Error:
        # The driver's message can quote the connection string; say only the code.
        raise BackupError("backup_role_missing") from None
    if row is None or not row[0]:
        raise BackupError("backup_role_missing")


def server_major(conninfo: str) -> int:
    try:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            row = conn.execute("SELECT current_setting('server_version_num')::int").fetchone()
    except psycopg.Error:
        raise BackupError("backup_role_missing") from None
    if row is None:
        raise BackupError("backup_role_missing")
    return int(row[0]) // 10000


def pg_dump_major(binary: str = "pg_dump") -> int:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, the binary is ours
            [binary, "--version"], capture_output=True, text=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        raise BackupError("pg_client_too_old") from None
    match = _PG_DUMP_VERSION.search(result.stdout)
    if match is None:
        raise BackupError("pg_client_too_old")
    return int(match.group(1))


def require_client_for(conninfo: str) -> None:
    if pg_dump_major() < server_major(conninfo):
        raise BackupError("pg_client_too_old")

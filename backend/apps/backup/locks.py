import contextlib
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from django.db import DatabaseError, connection

from apps.backup.errors import BackupError

BACKUP_LOCK_KEY: int = int.from_bytes(b"brvbckup", "big")


@contextmanager
def backup_lock() -> Iterator[None]:
    """Hold the session-level advisory lock that keeps back-ups and restores apart.

    Session level on the default connection, so it must run outside a transaction.
    """
    if connection.in_atomic_block:
        raise RuntimeError("backup_lock must be taken outside a transaction.")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [BACKUP_LOCK_KEY])
        row = cursor.fetchone()
    if row is None or not row[0]:
        raise BackupError("busy")
    try:
        yield
    finally:
        _unlock()


def _unlock() -> None:
    """Release the lock without ever replacing the body's exception.

    When the connection died during the body, the session lock died with it. When
    the unlock fails for any other database reason, closing the connection ends
    the session and so releases the lock as well.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [BACKUP_LOCK_KEY])
    except DatabaseError:
        connection.close()


@contextmanager
def database_lock(conninfo: str) -> Iterator[None]:
    """The same lock, on its own connection to the database `conninfo` names.

    For the steps of a restore that cannot use the default connection: the database
    is being replaced. Advisory locks belong to one database, so this one keeps out
    a back-up or sweep of *that* database (they take `backup_lock` there). Released
    when the body ends, also when it raised; if that fails, closing the connection
    releases it.
    """
    try:
        conn = psycopg.connect(conninfo, autocommit=True)
    except psycopg.Error:
        # The driver's message can quote the connection string; say only the code.
        raise BackupError("restore_failed") from None
    with conn:
        try:
            row = conn.execute("SELECT pg_try_advisory_lock(%s)", [BACKUP_LOCK_KEY]).fetchone()
        except psycopg.Error:
            raise BackupError("restore_failed") from None
        if row is None or not row[0]:
            raise BackupError("busy")
        try:
            yield
        finally:
            # Unlocked explicitly: the server ends a closed session (and so drops its
            # locks) only some moments after the client has gone.
            with contextlib.suppress(psycopg.Error):
                conn.execute("SELECT pg_advisory_unlock(%s)", [BACKUP_LOCK_KEY])

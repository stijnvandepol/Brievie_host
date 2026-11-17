from collections.abc import Iterator
from contextlib import contextmanager

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

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from django.db import connection, transaction

from apps.workspaces.rls import SETTING_NAME


def _current_setting() -> str:
    with connection.cursor() as c:
        c.execute("SELECT COALESCE(current_setting(%s, true), '')", [SETTING_NAME])
        row = c.fetchone()
    return str(row[0]) if row else ""


def _set_setting(value: str) -> None:
    with connection.cursor() as c:
        c.execute("SELECT set_config(%s, %s, true)", [SETTING_NAME, value])


@contextmanager
def workspace_context(workspace_id: UUID) -> Iterator[None]:
    """Run the block with row-level security scoped to one workspace.

    The setting is transaction-local, but it survives the release of a savepoint,
    so inside an outer transaction (ATOMIC_REQUESTS, a test) it would outlive this
    block. It is therefore restored explicitly on a normal exit. When the block
    fails, the savepoint is rolled back, which also rolls back the setting.
    """
    with transaction.atomic():
        previous = _current_setting()
        _set_setting(str(workspace_id))
        yield
        # A query that failed inside the block (and was caught there) marks the
        # savepoint for rollback; that rollback restores the setting, and no
        # further query is allowed before it.
        if not transaction.get_rollback():
            _set_setting(previous)

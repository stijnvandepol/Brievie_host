from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final, Literal
from uuid import UUID

from django.db import connection, transaction

from apps.workspaces.rls import ACTOR_SETTING, SETTING_NAME

SYSTEM: Final = "system"
NOBODY: Final = ""

# Who the policies see: a user's id, the system (reads and writes everything in the
# workspace), or nobody (an empty actor sees no letter, R6).
type Actor = UUID | Literal["system", ""]


def _actor_value(actor: Actor) -> str:
    if isinstance(actor, UUID):
        return str(actor)
    if actor in (SYSTEM, NOBODY):
        return actor
    raise ValueError(f"Not an actor: {actor!r}")


def _current_settings() -> tuple[str, str]:
    with connection.cursor() as c:
        c.execute(
            "SELECT COALESCE(current_setting(%s, true), ''),"
            " COALESCE(current_setting(%s, true), '')",
            [SETTING_NAME, ACTOR_SETTING],
        )
        row = c.fetchone()
    return (str(row[0]), str(row[1])) if row else ("", "")


def _set_settings(workspace: str, actor: str) -> None:
    with connection.cursor() as c:
        c.execute(
            "SELECT set_config(%s, %s, true), set_config(%s, %s, true)",
            [SETTING_NAME, workspace, ACTOR_SETTING, actor],
        )


def _set_actor(actor: str) -> None:
    with connection.cursor() as c:
        c.execute("SELECT set_config(%s, %s, true)", [ACTOR_SETTING, actor])


@contextmanager
def workspace_context(workspace_id: UUID, *, actor: Actor) -> Iterator[None]:
    """Run the block with row-level security scoped to one workspace and one actor.

    The settings are transaction-local, but they survive the release of a savepoint,
    so inside an outer transaction (ATOMIC_REQUESTS, a test) they would outlive this
    block. They are therefore restored explicitly on a normal exit. When the block
    fails, the savepoint is rolled back, which also rolls back the settings.

    A query that fails inside the block and is caught there must run in its own
    `transaction.atomic()`. ORM writes mark the block for rollback themselves, but
    a raw `cursor.execute` does not: PostgreSQL then rejects every later query in
    the transaction, including the restore of the settings.
    """
    value = _actor_value(actor)
    with transaction.atomic():
        previous = _current_settings()
        _set_settings(str(workspace_id), value)
        yield
        # A query that failed inside the block (and was caught there) marks the
        # savepoint for rollback; that rollback restores the settings, and no
        # further query is allowed before it.
        if not transaction.get_rollback():
            _set_settings(*previous)


def enter_workspace_for_transaction(workspace_id: UUID, *, actor: UUID) -> None:
    """Scope row-level security to one workspace and one user until the current
    transaction ends.

    For request handling: with ATOMIC_REQUESTS the request is one transaction, and
    a transaction-local setting ends with it, so it cannot leak into the next
    request on the same connection. Outside a transaction the setting would last
    for this one statement only, so that is refused. A request is never the system.
    """
    if not isinstance(actor, UUID):
        raise ValueError(f"A request runs as a user, not {actor!r}")
    if not connection.in_atomic_block:
        raise RuntimeError("enter_workspace_for_transaction() needs an open transaction.")
    _set_settings(str(workspace_id), str(actor))


@contextmanager
def system_actor() -> Iterator[None]:
    """Run the block as the system inside the current workspace, then restore the actor.

    For the writes that follow a user's own (the write order of S 4.9.3) and for checks
    that must see letters the user may not. Only the files in `ALLOWED_SYSTEM_CALLERS`
    (tests/test_actor_callers.py) call it.
    """
    with transaction.atomic():
        workspace, previous = _current_settings()
        if not workspace:
            raise RuntimeError("system_actor() needs a workspace context.")
        _set_actor(SYSTEM)
        yield
        if not transaction.get_rollback():
            _set_actor(previous)


def current_actor() -> UUID | Literal["system"] | None:
    """The actor the policies see now; None for an empty actor."""
    _workspace, actor = _current_settings()
    if actor == SYSTEM:
        return SYSTEM
    if not actor:
        return None
    return UUID(actor)

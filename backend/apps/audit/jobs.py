"""Background job of the audit log: removing rows older than the retention period."""

import logging
from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from procrastinate.contrib.django import app

from apps.pipeline.jobs import QUEUE
from apps.workspaces.models import Workspace

logger = logging.getLogger(__name__)


@app.periodic(cron="17 3 * * *")
@app.task(name="purge_audit_events", queue=QUEUE)
def purge_audit_events(timestamp: int) -> None:
    """Remove audit rows older than `BRIEVIE_AUDIT_RETENTION_DAYS`, one workspace at a time.

    Through the owner's purge functions (migration 0002), each in its own short
    transaction; they refuse a cut-off younger than 90 days.
    """
    cutoff = _cutoff(settings.BRIEVIE_AUDIT_RETENTION_DAYS)
    workspace_ids = list(Workspace.objects.values_list("id", flat=True))
    workspace_rows = 0
    for workspace_id in workspace_ids:
        workspace_rows += _call(
            "SELECT brievie_purge_workspace_audit(%s, %s)", (workspace_id, cutoff)
        )
    server_rows = _call("SELECT brievie_purge_server_audit(%s)", (cutoff,))
    logger.info(
        "Removed old audit rows: %d of workspaces, %d of the server", workspace_rows, server_rows
    )


def _cutoff(retention_days: int) -> datetime:
    """Now by the database's clock, minus the retention.

    The purge functions compare with the database's `now()` of their own (later)
    transaction, so a cut-off taken here is never younger than they allow.
    """
    with transaction.atomic(), connection.cursor() as c:
        c.execute("SELECT now() - make_interval(days => %s)", [retention_days])
        (cutoff,) = c.fetchone() or (None,)
    if not isinstance(cutoff, datetime):
        raise TypeError("The database gave no time.")
    return cutoff


def _call(sql: str, params: tuple[UUID, datetime] | tuple[datetime]) -> int:
    with transaction.atomic(), connection.cursor() as c:
        c.execute(sql, params)
        row = c.fetchone()
    return int(row[0]) if row else 0

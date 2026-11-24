"""The letter's cached task facts are a letter's only (archive spec AR7): the statement the
nightly repair uses (`apps.tasks.letter_sync.RECOMPUTE`, copied: a migration does not
import moving code) now joins no task of an own document, which keeps it at zero open
tasks. Run once per workspace as the system, so the backfill and the repair agree (every
existing row is a letter, so nothing changes today). Reversible: nothing to undo."""

from typing import Final

from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from apps.workspaces.context import SYSTEM, workspace_context

RECOMPUTE: Final = """
UPDATE documents_document d
SET task_open_count = f.open_count,
    task_wait_until = f.wait_until,
    task_overdue_from = f.overdue_from
FROM (
    SELECT l.id,
           COALESCE(a.open_count, 0)::integer AS open_count,
           a.wait_until,
           a.overdue_from
    FROM documents_document l
    LEFT JOIN (
        SELECT t.document_id,
               count(*) AS open_count,
               CASE WHEN count(*) = count(t.waiting_until) THEN min(t.waiting_until) END
                   AS wait_until,
               min(GREATEST(t.deadline + 1, t.waiting_until))
                   FILTER (WHERE t.deadline IS NOT NULL) AS overdue_from
        FROM tasks_task t
        WHERE t.workspace_id = %(workspace)s AND t.status = 'open'
          AND t.document_id IS NOT NULL
        GROUP BY t.document_id
    ) a ON a.document_id = l.id AND l.kind = 'letter'
    WHERE l.workspace_id = %(workspace)s
) f
WHERE d.id = f.id AND d.workspace_id = %(workspace)s
  AND (d.task_open_count, d.task_wait_until, d.task_overdue_from)
      IS DISTINCT FROM (f.open_count, f.wait_until, f.overdue_from)
"""


def fill(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    workspaces = apps.get_model("workspaces", "Workspace")
    for workspace_id in list(workspaces.objects.values_list("id", flat=True)):
        with (
            workspace_context(workspace_id, actor=SYSTEM),
            schema_editor.connection.cursor() as cursor,
        ):
            cursor.execute(RECOMPUTE, {"workspace": workspace_id})


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0028_archive_event_kinds"),
        ("workspaces", "0010_unread_and_lingering"),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]

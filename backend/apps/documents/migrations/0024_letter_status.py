"""Letter status (spec S 4.2, 10): three facts about a letter's tasks cached on the letter
(`task_open_count`, `task_wait_until`, `task_overdue_from`), the partial index of the
letters with open tasks, `DocumentEvent.on_date` and four event kinds.

The facts are filled from the tasks per workspace, as the system (the document policies
bind the migrating owner too), with the statement `apps.tasks.letter_sync.RECOMPUTE` also
uses (copied: a migration does not import moving code). Reversible: the reverse drops the
columns (they are derived)."""

from typing import Final

from django.apps.registry import Apps
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from apps.workspaces.context import SYSTEM, workspace_context

# A database default too, so a raw INSERT that leaves the column out (the performance
# seed, a restore script) keeps working; not `db_default` (as documents 0017).
SET_DEFAULT: Final = "ALTER TABLE documents_document ALTER COLUMN task_open_count SET DEFAULT 0"
DROP_DEFAULT: Final = "ALTER TABLE documents_document ALTER COLUMN task_open_count DROP DEFAULT"

# Per letter of the workspace: the open tasks counted, the earliest waiting date when every
# open task waits, and the first day one is late (GREATEST skips a missing waiting date).
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
    ) a ON a.document_id = l.id
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
        ("documents", "0023_read_mark_opened"),
        ("tasks", "0006_waiting_and_snooze"),
        ("workspaces", "0010_unread_and_lingering"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="task_open_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="document",
            name="task_overdue_from",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="task_wait_until",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentevent",
            name="on_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="documentevent",
            name="kind",
            field=models.CharField(
                choices=[
                    ("label_added", "Label Added"),
                    ("label_removed", "Label Removed"),
                    ("addressee_added", "Addressee Added"),
                    ("addressee_removed", "Addressee Removed"),
                    ("confirmed", "Confirmed"),
                    ("task_completed", "Task Completed"),
                    ("task_reopened", "Task Reopened"),
                    ("reopened", "Reopened"),
                    ("made_private", "Made Private"),
                    ("made_public", "Made Public"),
                    ("share_added", "Share Added"),
                    ("share_removed", "Share Removed"),
                    ("access_granted", "Access Granted"),
                    ("emergency_opened", "Emergency Opened"),
                    ("private_skipped", "Private Skipped"),
                    ("trashed", "Trashed"),
                    ("restored", "Restored"),
                    ("merged", "Merged"),
                    ("duplicate_dismissed", "Duplicate Dismissed"),
                    ("task_moved", "Task Moved"),
                    ("task_assigned", "Task Assigned"),
                    ("task_unassigned", "Task Unassigned"),
                    ("task_deadline_set", "Task Deadline Set"),
                    ("task_waiting", "Task Waiting"),
                    ("task_waiting_ended", "Task Waiting Ended"),
                    ("letter_handled", "Letter Handled"),
                    ("letter_reopened", "Letter Reopened"),
                ],
                max_length=30,
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(("status", "done"), ("task_open_count__gt", 0)),
                fields=["workspace", "-created_at"],
                name="documents_document_open_tasks",
            ),
        ),
        migrations.RunSQL(SET_DEFAULT, DROP_DEFAULT),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]

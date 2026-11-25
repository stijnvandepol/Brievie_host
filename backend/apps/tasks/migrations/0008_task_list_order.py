"""The task list in its own order (`list_tasks`: deadline, undated last, then creation;
DECISIONS 2026-11-25, list headroom). Read in this order, the task policy runs only until
the page is full instead of for every task before a sort.

A plain `CREATE INDEX`: a SHARE lock on `tasks_task` (reads go on, writes wait) for the
moment the build takes. `./brievie update` and the compose `init` service migrate with the
app stopped. Reversible (drops the index)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0007_snooze_cascade_in_database"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="task",
            index=models.Index(
                fields=["workspace", "deadline", "created_at", "id"],
                name="tasks_task_list_order",
            ),
        ),
    ]

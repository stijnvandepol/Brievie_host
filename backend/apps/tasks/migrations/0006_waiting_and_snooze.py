"""Letter status (spec S 5, 10): `Task.waiting_until` ("wacht op reactie tot", shared) and
`TaskSnooze` ("uitgesteld tot", one row per task and member) under forced row-level
security: the workspace policy, the task's own policies (a row follows its task) and a
restrictive own-row policy (`task_snooze_own`: a member reads and writes only their own
rows, the system all)."""

import uuid
from typing import Final

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.workspaces.rls import (
    TASK_SNOOZE_POLICY,
    enable_rls,
    own_rows_policy,
    restrict_to_visible_tasks,
)

TABLE: Final = "tasks_tasksnooze"


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0005_my_tasks"),
        ("workspaces", "0010_unread_and_lingering"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="waiting_until",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="TaskSnooze",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("until", models.DateField()),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="snoozes",
                        to="tasks.task",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="workspaces.workspace"
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["user", "task"], name="tasks_tasksnooze_user")],
                "constraints": [
                    models.UniqueConstraint(fields=("task", "user"), name="tasks_tasksnooze_unique")
                ],
            },
        ),
        enable_rls(TABLE),
        restrict_to_visible_tasks(TABLE),
        own_rows_policy(TABLE, TASK_SNOOZE_POLICY),
    ]

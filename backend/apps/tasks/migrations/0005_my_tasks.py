import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

# A database default for the new text column too, so a raw INSERT that leaves it out (the
# performance seed, a restore script) keeps working; not `db_default` (as documents 0017).
SET_DEFAULT = "ALTER TABLE tasks_task ALTER COLUMN term_quote SET DEFAULT ''"
DROP_DEFAULT = "ALTER TABLE tasks_task ALTER COLUMN term_quote DROP DEFAULT"


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0018_task_event_kinds"),
        ("tasks", "0004_task_visibility"),
        ("workspaces", "0008_workspace_summary_language"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="task",
            name="assigned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="completed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="term_quote",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(
                condition=models.Q(("status", "open")),
                fields=["workspace", "deadline"],
                name="tasks_task_open",
            ),
        ),
        migrations.AddConstraint(
            model_name="task",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gte", 0)), name="tasks_task_amount_not_negative"
            ),
        ),
        migrations.RunSQL(SET_DEFAULT, DROP_DEFAULT),
    ]

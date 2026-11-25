"""The task type `expiry` (the reminder of an own document's end date) and the partial unique
index `tasks_task_one_reminder`: at most one open reminder per document (archive spec
S 7.1). Letters' tasks are untouched."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0008_task_list_order"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="type",
            field=models.CharField(
                choices=[
                    ("pay", "Betalen"),
                    ("respond", "Reageren"),
                    ("object", "Bezwaar maken"),
                    ("apply", "Aanvragen"),
                    ("inspection", "Keuring"),
                    ("cancel", "Opzeggen"),
                    ("renew", "Verlengen"),
                    ("file_tax", "Aangifte"),
                    ("other", "Overig"),
                    ("expiry", "Verloopt"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="task",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "open"), ("type", "expiry")),
                fields=("document",),
                name="tasks_task_one_reminder",
            ),
        ),
    ]

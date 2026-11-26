"""History origin `watch_folder`: a scan folder's person or department (intake Task 9)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0034_year_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documentevent",
            name="origin",
            field=models.CharField(
                choices=[
                    ("user", "User"),
                    ("rule", "Rule"),
                    ("suggestion", "Suggestion"),
                    ("undo", "Undo"),
                    ("ai", "Ai"),
                    ("name_match", "Name Match"),
                    ("rule_autopilot", "Rule Autopilot"),
                    ("merge", "Merge"),
                    ("system", "System"),
                    ("watch_folder", "Watch Folder"),
                ],
                default="user",
                max_length=20,
            ),
        ),
    ]

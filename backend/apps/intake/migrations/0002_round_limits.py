"""The scan folder's round limits (security review of PR #64, M1 and M4)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("intake", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="scanfile",
            name="first_seen_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="scanfoldersettings",
            name="skipped_count",
            field=models.IntegerField(default=0),
        ),
    ]

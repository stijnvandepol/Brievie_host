"""The former account frozen at switch-off (security review of PR #43, point 2)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0005_person_managed_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="person",
            name="former_email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="person",
            name="former_last_app_use_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="person",
            name="former_last_sign_in_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

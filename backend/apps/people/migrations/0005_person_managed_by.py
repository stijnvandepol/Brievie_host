"""Person.managed_by: "Beheerd door" (Mensen slice C), only on a person without an account."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0004_person_account_state"),
        ("workspaces", "0008_workspace_summary_language"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="person",
            name="managed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="person",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("managed_by__isnull", True), ("user__isnull", True), _connector="OR"
                ),
                name="people_person_managed_only_without_account",
            ),
        ),
    ]

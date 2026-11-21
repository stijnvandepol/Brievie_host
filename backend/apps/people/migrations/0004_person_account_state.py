"""Two display columns for Mensen (spec 3.1, 7): when a person's account was switched
off, and which account it was. No data moves; nothing reads them for sight (M4)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0003_departments"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="person",
            name="deactivated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="person",
            name="former_user",
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
                condition=models.Q(former_user__isnull=True)
                | models.Q(deactivated_at__isnull=False),
                name="people_person_former_user_needs_deactivated",
            ),
        ),
    ]

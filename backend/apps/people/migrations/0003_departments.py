import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.workspaces.rls import enable_rls


class Migration(migrations.Migration):
    dependencies = [
        ("people", "0002_persons_for_members"),
        ("workspaces", "0006_visibility_switches"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="person",
            name="kind",
            field=models.CharField(
                choices=[("person", "Persoon"), ("department", "Afdeling")],
                default="person",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="DepartmentMember",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_lead", models.BooleanField(default=False)),
                (
                    "department",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="department_members",
                        to="people.person",
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
                "constraints": [
                    models.UniqueConstraint(
                        fields=("department", "user"), name="people_departmentmember_unique"
                    ),
                ],
            },
        ),
        enable_rls("people_departmentmember"),
    ]

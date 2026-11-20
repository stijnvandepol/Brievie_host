from django.db import migrations

from apps.workspaces.rls import task_visibility_policy


class Migration(migrations.Migration):
    dependencies = [
        ("tasks", "0003_task_created_by"),
        ("documents", "0010_visibility"),
    ]

    operations = [task_visibility_policy()]

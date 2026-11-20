from django.db import migrations

from apps.workspaces.rls import actor_functions


class Migration(migrations.Migration):
    dependencies = [
        ("workspaces", "0004_workspace_allow_member_export"),
    ]

    operations = [actor_functions()]

from django.db import migrations

from apps.workspaces.visibility_sql import DROP_VISIBILITY_FUNCTIONS, visibility_functions_sql_0007


class Migration(migrations.Migration):
    dependencies = [
        ("workspaces", "0006_visibility_switches"),
        # brievie_my_post_ids and brievie_covered_post_ids read the department members.
        ("people", "0003_departments"),
    ]

    operations = [
        migrations.RunSQL(
            sql=tuple(visibility_functions_sql_0007()),
            reverse_sql=tuple(DROP_VISIBILITY_FUNCTIONS),
        ),
    ]

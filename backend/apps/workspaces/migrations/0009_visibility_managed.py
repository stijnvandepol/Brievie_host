"""Mensen slice C ("Beheerd door"): `brievie_my_post_ids` gives a member the post of the
persons without an account they manage, and `brievie_covered_post_ids` counts those
persons as read through, so the sorters no longer get it. Replaced in place (the
policies call both); the reverse puts back the text of 0007. No manager set: nobody's
sight changes (proved by tests/test_people_migration_viewers.py)."""

from django.db import migrations

from apps.workspaces.visibility_sql import (
    managed_post_functions_reverse_sql,
    managed_post_functions_sql,
)


class Migration(migrations.Migration):
    dependencies = [
        ("workspaces", "0008_workspace_summary_language"),
        ("people", "0005_person_managed_by"),
    ]

    operations = [
        migrations.RunSQL(
            sql=tuple(managed_post_functions_sql()),
            reverse_sql=tuple(managed_post_functions_reverse_sql()),
        ),
    ]

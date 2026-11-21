"""Navigation (spec S 5.2, S 6): `Membership.unread_from`, the moment "Ongelezen" starts
for a member (every existing member gets the moment of this migration, so nobody starts
with every old letter unread), and the "Blijft liggen" settings of a workspace: on for an
organisation, off at home; 2 days in the Inbox, 5 days not opened."""

import django.utils.timezone
from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

# Database defaults too, so a raw INSERT that leaves the columns out (a seed, a restore
# script) keeps working; the models keep Python defaults (no RETURNING, see 0008).
SET_DEFAULTS = (
    "ALTER TABLE workspaces_membership ALTER COLUMN unread_from SET DEFAULT now()",
    "ALTER TABLE workspaces_workspace ALTER COLUMN lingering_enabled SET DEFAULT false",
    "ALTER TABLE workspaces_workspace ALTER COLUMN lingering_inbox_days SET DEFAULT 2",
    "ALTER TABLE workspaces_workspace ALTER COLUMN lingering_unopened_days SET DEFAULT 5",
)
DROP_DEFAULTS = (
    "ALTER TABLE workspaces_membership ALTER COLUMN unread_from DROP DEFAULT",
    "ALTER TABLE workspaces_workspace ALTER COLUMN lingering_enabled DROP DEFAULT",
    "ALTER TABLE workspaces_workspace ALTER COLUMN lingering_inbox_days DROP DEFAULT",
    "ALTER TABLE workspaces_workspace ALTER COLUMN lingering_unopened_days DROP DEFAULT",
)


def lingering_for_organisations(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    workspaces = apps.get_model("workspaces", "Workspace")
    workspaces.objects.filter(kind="business").update(lingering_enabled=True)


class Migration(migrations.Migration):
    dependencies = [
        ("workspaces", "0009_visibility_managed"),
    ]

    operations = [
        migrations.AddField(
            model_name="membership",
            name="unread_from",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AddField(
            model_name="workspace",
            name="lingering_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="workspace",
            name="lingering_inbox_days",
            field=models.PositiveSmallIntegerField(default=2),
        ),
        migrations.AddField(
            model_name="workspace",
            name="lingering_unopened_days",
            field=models.PositiveSmallIntegerField(default=5),
        ),
        migrations.RunSQL(SET_DEFAULTS, DROP_DEFAULTS),
        migrations.RunPython(lingering_for_organisations, migrations.RunPython.noop),
    ]

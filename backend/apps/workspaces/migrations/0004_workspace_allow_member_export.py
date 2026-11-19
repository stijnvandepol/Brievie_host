from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor


def off_for_business(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """D 6.3: exporting is on at home and off in Zakelijk until an admin allows it."""
    apps.get_model("workspaces", "Workspace").objects.filter(kind="business").update(
        allow_member_export=False
    )


class Migration(migrations.Migration):
    dependencies = [
        ("workspaces", "0003_invitation"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="allow_member_export",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(off_for_business, migrations.RunPython.noop),
    ]

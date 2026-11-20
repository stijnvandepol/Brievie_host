from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

ROLE_CHOICES = [("admin", "Beheerder"), ("member", "Lid"), ("viewer", "Alleen lezen")]


def visibility_defaults(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """S 4.16: every existing workspace keeps "Iedereen ziet alle post", whatever its kind;
    the privacy settings take the kind's values (Thuis: Zorg is private, no emergency
    access; Organisatie the other way round)."""
    workspaces = apps.get_model("workspaces", "Workspace").objects
    workspaces.update(everyone_sees_all=True, departments_enabled=False, admins_read_all=True)
    workspaces.filter(kind="home").update(care_is_private=True, emergency_access=False)
    workspaces.filter(kind="business").update(care_is_private=False, emergency_access=True)


class Migration(migrations.Migration):
    dependencies = [
        ("workspaces", "0005_actor_functions"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="everyone_sees_all",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="workspace",
            name="departments_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="workspace",
            name="admins_read_all",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="workspace",
            name="care_is_private",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="workspace",
            name="emergency_access",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="membership",
            name="sorts_post",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="workspace",
            name="kind",
            field=models.CharField(
                choices=[("home", "Thuis"), ("business", "Organisatie")], max_length=10
            ),
        ),
        migrations.AlterField(
            model_name="membership",
            name="role",
            field=models.CharField(choices=ROLE_CHOICES, max_length=10),
        ),
        migrations.AlterField(
            model_name="invitation",
            name="role",
            field=models.CharField(choices=ROLE_CHOICES, default="member", max_length=10),
        ),
        migrations.RunPython(visibility_defaults, migrations.RunPython.noop),
    ]

from django.db import migrations, models

# A database default too, so a raw INSERT that leaves the column out (a seed, a restore
# script) keeps working. Not `db_default`: the model's default stays a Python one, so
# Django never inserts with RETURNING (see the visibility switches in models.py).
SET_DEFAULT = "ALTER TABLE workspaces_workspace ALTER COLUMN summary_language SET DEFAULT 'nl'"
DROP_DEFAULT = "ALTER TABLE workspaces_workspace ALTER COLUMN summary_language DROP DEFAULT"


class Migration(migrations.Migration):
    dependencies = [
        ("workspaces", "0007_visibility_functions"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="summary_language",
            field=models.CharField(
                choices=[("nl", "Nederlands"), ("en", "English")], default="nl", max_length=2
            ),
        ),
        migrations.RunSQL(SET_DEFAULT, DROP_DEFAULT),
    ]

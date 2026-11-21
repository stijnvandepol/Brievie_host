from django.db import migrations, models


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
    ]

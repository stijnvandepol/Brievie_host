from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_setup_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_server_admin",
            field=models.BooleanField(default=False),
        ),
    ]

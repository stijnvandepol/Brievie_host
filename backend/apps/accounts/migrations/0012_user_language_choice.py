from django.apps.registry import Apps
from django.db import migrations, models


def forget_the_old_default(apps: Apps, schema_editor: object) -> None:
    """Nobody could choose a language before: the stored "nl" was only the default, so
    every account starts without a choice (its Accept-Language decides)."""
    user = apps.get_model("accounts", "User")
    user.objects.filter(language="nl").update(language="")
    user.objects.exclude(language__in=["", "nl", "en"]).update(language="")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_signin_failures"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="language",
            field=models.CharField(
                blank=True,
                choices=[("nl", "Nederlands"), ("en", "English")],
                default="",
                max_length=10,
            ),
        ),
        migrations.RunPython(forget_the_old_default, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="language",
            field=models.CharField(
                blank=True,
                choices=[("nl", "Nederlands"), ("en", "English")],
                default="",
                max_length=2,
            ),
        ),
    ]

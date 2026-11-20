from django.db import migrations, models


class Migration(migrations.Migration):
    """The autopilot notice version an administrator agreed to.

    Rows that exist hold 1 (the notice before rule labels); a new row holds 0 (never).
    """

    dependencies = [
        ("pipeline", "0004_aisettings_auto_confirm_aisettings_auto_organise"),
    ]

    operations = [
        migrations.AddField(
            model_name="aisettings",
            name="autopilot_notice_version",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AlterField(
            model_name="aisettings",
            name="autopilot_notice_version",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]

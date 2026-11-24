"""Security review of PR #52, point 1: wrong codes are counted since the last right code,
so the moment kept is the last wrong one."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0017_second_factor_codes"),
    ]

    operations = [
        migrations.RenameField(
            model_name="secondfactor",
            old_name="first_failed_at",
            new_name="last_failed_at",
        ),
    ]

"""The notice `private_without_readers_unnamed` (choices only)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0012_user_language_choice"),
    ]

    operations = [
        migrations.AlterField(
            model_name="accountnotice",
            name="code",
            field=models.CharField(
                choices=[
                    ("password_reset_created", "Herstellink gemaakt"),
                    ("password_reset_completed", "Wachtwoord opnieuw ingesteld"),
                    ("password_reset_by_command", "Wachtwoord opnieuw ingesteld op de server"),
                    ("sessions_ended", "Sessies beëindigd"),
                    ("device_revoked", "Apparaat ingetrokken"),
                    ("device_paired", "Apparaat gekoppeld"),
                    ("second_factor_cleared", "Tweestapsverificatie gewist"),
                    ("server_admin_granted", "Serverbeheerder gemaakt"),
                    ("server_admin_revoked", "Geen serverbeheerder meer"),
                    ("emergency_access_switched_on", "Noodtoegang aangezet"),
                    ("emergency_access_switched_off", "Noodtoegang uitgezet"),
                    ("emergency_access_used", "Noodtoegang gebruikt"),
                    ("private_without_readers", "Privépost zonder lezers"),
                    (
                        "private_without_readers_unnamed",
                        "Privépost zonder lezers, brief niet genoemd",
                    ),
                ],
                max_length=40,
            ),
        ),
    ]

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_usersession"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountNotice",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "code",
                    models.CharField(
                        choices=[
                            ("password_reset_created", "Herstellink gemaakt"),
                            ("password_reset_completed", "Wachtwoord opnieuw ingesteld"),
                            ("sessions_ended", "Sessies beëindigd"),
                            ("device_revoked", "Apparaat ingetrokken"),
                            ("second_factor_cleared", "Tweestapsverificatie gewist"),
                            ("emergency_access_switched_on", "Noodtoegang aangezet"),
                            ("emergency_access_switched_off", "Noodtoegang uitgezet"),
                            ("emergency_access_used", "Noodtoegang gebruikt"),
                            ("private_without_readers", "Privépost zonder lezers"),
                        ],
                        max_length=40,
                    ),
                ),
                ("actor_name", models.CharField(blank=True, max_length=150)),
                ("values", models.JSONField(blank=True, default=dict)),
                ("seen_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["user", "seen_at"], name="accounts_notice_unseen")
                ],
            },
        ),
    ]

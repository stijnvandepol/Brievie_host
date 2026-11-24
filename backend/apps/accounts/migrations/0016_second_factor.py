"""Two-step sign-in (plan Task 2): the second factor, its recovery codes, since when a rule
asks an account for one, and whether a session passed the second step."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0015_notice_workspace"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="second_factor_required_since",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="usersession",
            name="second_factor",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="RecoveryCode",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code_hash", models.CharField(max_length=64, unique=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recovery_codes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="SecondFactor",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("secret_sealed", models.BinaryField()),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("last_used_step", models.BigIntegerField(default=0)),
                ("failed_count", models.IntegerField(default=0)),
                ("first_failed_at", models.DateTimeField(blank=True, null=True)),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
                ("lockouts", models.IntegerField(default=0)),
                ("pending_secret_sealed", models.BinaryField(blank=True, null=True)),
                ("pending_since", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="second_factor",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
    ]

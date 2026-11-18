import uuid

import django.db.models.deletion
from django.apps.registry import Apps
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.db import migrations, models
from django.utils import timezone


def record_existing_sessions(apps: Apps, schema_editor: object) -> None:
    """Record every unexpired signed-in session, so the upgrade signs nobody out.

    The middleware signs out a session without a record. The historical `Session`
    model has no `get_decoded()`; Django's session store decodes the data. Rows that
    are recorded already are left alone, so running it twice is safe.
    """
    session_model = apps.get_model("sessions", "Session")
    user_session_model = apps.get_model("accounts", "UserSession")
    user_model = apps.get_model("accounts", "User")
    store = SessionStore()
    now = timezone.now()
    for row in session_model.objects.filter(expire_date__gt=now).iterator():
        user_id = store.decode(row.session_data).get("_auth_user_id")
        try:
            user_uuid = uuid.UUID(str(user_id))
        except ValueError:
            continue
        if not user_model.objects.filter(pk=user_uuid).exists():
            continue
        user_session_model.objects.get_or_create(
            session_key=row.session_key,
            defaults={
                "user_id": user_uuid,
                "method": "password",
                "created_via": "password",
                "last_used_at": now,
                "browser": "",
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_first_server_admin"),
        ("sessions", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserSession",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("session_key", models.CharField(max_length=40, unique=True)),
                (
                    "method",
                    models.CharField(
                        choices=[("password", "Wachtwoord"), ("sso", "Inloggen via organisatie")],
                        max_length=10,
                    ),
                ),
                (
                    "created_via",
                    models.CharField(
                        choices=[
                            ("password", "Inloggen"),
                            ("setup", "Installatie"),
                            ("invitation", "Uitnodiging"),
                            ("reset", "Wachtwoord opnieuw ingesteld"),
                        ],
                        default="password",
                        max_length=20,
                    ),
                ),
                ("last_used_at", models.DateTimeField()),
                ("client_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("browser", models.CharField(blank=True, max_length=100)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="web_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.RunPython(record_existing_sessions, migrations.RunPython.noop),
    ]

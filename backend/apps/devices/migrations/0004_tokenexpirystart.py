import uuid

from django.db import migrations, models
from django.db.migrations.state import StateApps
from django.utils import timezone


def record_install_moment(apps: StateApps, schema_editor: object) -> None:
    """Note when this update was installed (D 14); a device counts its idle time from the
    later of its last use and this moment. Keeps an earlier row, so it is safe to run twice."""
    start_model = apps.get_model("devices", "TokenExpiryStart")
    if not start_model.objects.exists():
        start_model.objects.create(started_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [
        ("devices", "0003_pairing_proof"),
    ]

    operations = [
        migrations.CreateModel(
            name="TokenExpiryStart",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("started_at", models.DateTimeField()),
                ("singleton", models.BooleanField(default=True, editable=False)),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("singleton",), name="devices_tokenexpirystart_singleton"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("singleton", True)),
                        name="devices_tokenexpirystart_singleton_true",
                    ),
                ],
            },
        ),
        migrations.RunPython(record_install_moment, migrations.RunPython.noop),
    ]

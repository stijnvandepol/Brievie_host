"""The reader reason managed (choices only, no change in the database)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0020_new_person_suggestion"),
    ]

    operations = [
        migrations.AlterField(
            model_name="documentreader",
            name="reason",
            field=models.CharField(
                choices=[
                    ("addressee", "Addressee"),
                    ("department", "Department"),
                    ("share", "Share"),
                    ("emergency", "Emergency"),
                    ("managed", "Managed"),
                ],
                max_length=20,
            ),
        ),
    ]

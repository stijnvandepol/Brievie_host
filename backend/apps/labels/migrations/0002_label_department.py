"""A label may belong to a department (S 4.6): one nullable column, no data change."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("labels", "0001_initial"),
        ("people", "0003_departments"),
    ]

    operations = [
        migrations.AddField(
            model_name="label",
            name="department",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="people.person",
            ),
        ),
    ]

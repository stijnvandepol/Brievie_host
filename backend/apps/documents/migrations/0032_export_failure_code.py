"""`MemberExport.failure_code`: why an export failed (`too_large`, `not_enough_space`;
security review of PR #54, point 2). A database default '' too, for raw inserts."""

from typing import Final

from django.db import migrations, models

SET_DEFAULT: Final = "ALTER TABLE documents_memberexport ALTER COLUMN failure_code SET DEFAULT ''"
DROP_DEFAULT: Final = "ALTER TABLE documents_memberexport ALTER COLUMN failure_code DROP DEFAULT"


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0031_export_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="memberexport",
            name="failure_code",
            field=models.CharField(blank=True, default="", max_length=30),
        ),
        migrations.RunSQL(SET_DEFAULT, DROP_DEFAULT),
    ]

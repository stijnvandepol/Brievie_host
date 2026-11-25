"""The original of an uploaded file (intake plan Task 1): its kind, checksum, size and
base name. Database defaults too, for raw inserts; not `db_default` (as documents 0017).
The partial index holds only uploaded files: a later duplicate check is a query."""

from typing import Final

from django.db import migrations, models

SET_DEFAULTS: Final = (
    "ALTER TABLE documents_document "
    "ALTER COLUMN original_kind SET DEFAULT '', "
    "ALTER COLUMN original_sha256 SET DEFAULT '', "
    "ALTER COLUMN original_name SET DEFAULT ''"
)
DROP_DEFAULTS: Final = (
    "ALTER TABLE documents_document "
    "ALTER COLUMN original_kind DROP DEFAULT, "
    "ALTER COLUMN original_sha256 DROP DEFAULT, "
    "ALTER COLUMN original_name DROP DEFAULT"
)


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0032_export_failure_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="original_kind",
            field=models.CharField(
                blank=True,
                choices=[("", "Geen"), ("pdf", "PDF"), ("jpeg", "JPEG"), ("png", "PNG")],
                default="",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="original_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="document",
            name="original_size",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="original_name",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.RunSQL(SET_DEFAULTS, DROP_DEFAULTS),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(("original_sha256", ""), _negated=True),
                fields=["workspace", "original_sha256"],
                name="documents_document_original",
            ),
        ),
    ]

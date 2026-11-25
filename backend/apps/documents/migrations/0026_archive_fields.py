"""Archive (spec S 4): `kind`, `title`, `expires_on`, `remind_days`, `retain_until` on the
document, and the five types of own documents.

Every existing row becomes a letter. The three not-null columns also get a database
default, so a raw INSERT that leaves them out (the performance seed, a restore script)
keeps working; not `db_default` (as documents 0017 and 0024)."""

from typing import Final

from django.db import migrations, models

SET_DEFAULTS: Final = (
    "ALTER TABLE documents_document ALTER COLUMN kind SET DEFAULT 'letter',"
    " ALTER COLUMN title SET DEFAULT '', ALTER COLUMN remind_days SET DEFAULT 30"
)
DROP_DEFAULTS: Final = (
    "ALTER TABLE documents_document ALTER COLUMN kind DROP DEFAULT,"
    " ALTER COLUMN title DROP DEFAULT, ALTER COLUMN remind_days DROP DEFAULT"
)


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0025_policy_covering_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="expires_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="kind",
            field=models.CharField(
                choices=[("letter", "Brief"), ("document", "Document")],
                default="letter",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="remind_days",
            field=models.PositiveSmallIntegerField(default=30),
        ),
        migrations.AddField(
            model_name="document",
            name="retain_until",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="title",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AlterField(
            model_name="document",
            name="document_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("invoice", "Factuur"),
                    ("assessment", "Aanslag"),
                    ("decision", "Beschikking"),
                    ("bill", "Rekening"),
                    ("reminder", "Herinnering"),
                    ("demand", "Aanmaning"),
                    ("policy", "Polis"),
                    ("annual_statement", "Jaaropgave"),
                    ("payslip", "Loonstrook"),
                    ("inspection", "Keuring"),
                    ("contract", "Contract"),
                    ("receipt", "Bonnetje"),
                    ("information", "Informatie"),
                    ("other", "Overig"),
                    ("identity", "Identiteitsbewijs"),
                    ("diploma", "Diploma"),
                    ("warranty", "Garantiebewijs"),
                    ("tax_return", "Belastingaangifte"),
                    ("medical", "Medisch"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.RunSQL(SET_DEFAULTS, DROP_DEFAULTS),
    ]

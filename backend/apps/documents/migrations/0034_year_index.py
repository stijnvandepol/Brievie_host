"""The archive's year filter on plain columns (DECISIONS 2026-11-25, archive list speed):
`letter_date`, else `created_at` between local midnights. A plain `CREATE INDEX`: updates
migrate with the app stopped, and it takes seconds at 100,000 letters."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0033_original_file"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                fields=["workspace", "letter_date", "created_at"],
                name="documents_document_year",
            ),
        ),
    ]

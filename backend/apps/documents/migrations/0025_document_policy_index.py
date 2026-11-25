"""A narrow index holding the letter policy's columns and the trash (DECISIONS
2026-11-25, list headroom).

A pass over every visible letter (the counts of `listPersons` and `listLabels`) and the
policy check of a task's letter (`tasks_task`'s policy) read the letter rows only for
these columns. The rows are wide (the text and the search vector), so at 100,000 letters
such a pass read about 230 MB; with the columns in an index PostgreSQL answers it from
the index alone (an index-only scan).

A plain `CREATE INDEX`: it takes a SHARE lock on `documents_document` (reads go on,
writes wait) for the seconds the build takes. `./brievie update` and the compose `init`
service migrate with the app stopped, so nothing waits. Reversible (drops the index)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0024_letter_status"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                fields=["workspace", "id"],
                include=["visibility", "addressee_ids", "scanned_by", "status", "trashed_at"],
                name="documents_document_policy",
            ),
        ),
    ]

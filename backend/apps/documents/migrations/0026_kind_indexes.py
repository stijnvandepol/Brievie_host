"""The three partial indexes of post that waits (Inbox "Naam kiezen", "Nog niet geopend",
opened today) name `kind = 'letter'` in their condition, as the queries now do (archive plan
Task 2): an own document is never post that waits. A plain rebuild of three small partial
indexes."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0025_archive_fields"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="document",
            name="documents_document_unassigned",
        ),
        migrations.RemoveIndex(
            model_name="document",
            name="documents_document_unopened",
        ),
        migrations.RemoveIndex(
            model_name="document",
            name="documents_document_opened",
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(("addressee_ids", []), ("kind", "letter"), ("status", "done")),
                fields=["workspace", "-created_at"],
                name="documents_document_unassigned",
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(
                    ("addressee_opened_at__isnull", True),
                    ("kind", "letter"),
                    ("status", "done"),
                    models.Q(("addressee_ids", []), _negated=True),
                    ("trashed_at__isnull", True),
                ),
                fields=["workspace", "confirmed_at"],
                name="documents_document_unopened",
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(
                    ("addressee_opened_at__isnull", False),
                    ("kind", "letter"),
                    ("status", "done"),
                    models.Q(("addressee_ids", []), _negated=True),
                    ("trashed_at__isnull", True),
                ),
                fields=["workspace", "addressee_opened_at"],
                name="documents_document_opened",
            ),
        ),
    ]

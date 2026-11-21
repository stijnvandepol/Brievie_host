"""Security review of PR #44: a read mark says whether it is an opening of the confirmed
letter (`opened_at`); only those count for "opened by an addressee". Marks written before
this are not openings (a scan or a bulk "Alles gelezen" is indistinguishable from them).
The partial index serves "opened today" of "Nog niet geopend" (day-granular, point 2)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0022_read_marks"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentreadmark",
            name="opened_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(("addressee_opened_at__isnull", False), ("status", "done"))
                & ~models.Q(addressee_ids=[])
                & models.Q(trashed_at__isnull=True),
                fields=["workspace", "addressee_opened_at"],
                name="documents_document_opened",
            ),
        ),
    ]

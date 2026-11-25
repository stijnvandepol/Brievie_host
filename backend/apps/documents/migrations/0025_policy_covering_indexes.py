"""Indexes that hold the letter policy's columns (DECISIONS 2026-11-25, list headroom).

A count over the visible letters, and the policy check of a task's letter, read a
letter's row only for the policy's few columns and the trash; the rows are wide (the text
and the search vector), so at 100,000 letters one pass read about 230 MB. With the columns
in the index PostgreSQL answers from the index alone (an index-only scan):

- `documents_document_policy` (new): every letter, with the policy's columns, the trash
  and the facts the Inbox and letter-status counts read;
- `documents_document_review`: now by arrival, with the policy's columns (its count, the
  part older than "Blijft liggen"'s day, and that list oldest first);
- `documents_document_unassigned`: the same key, now with the policy's columns.

Plain `CREATE INDEX` / `DROP INDEX`: each takes a lock on `documents_document` that lets
reads go on and makes writes wait for the seconds a build takes at 100,000 letters (the
two partial indexes: a fraction of that). `./brievie update` and the compose `init`
service migrate with the app stopped, so nothing waits. No column is added. Reversible:
the reverse puts the two partial indexes back as they were and drops the new one."""

from django.db import migrations, models

POLICY = ("visibility", "addressee_ids", "scanned_by", "status", "trashed_at")
COUNTED = (
    "confirmed_by_ai",
    "has_automatic_link",
    "task_open_count",
    "task_wait_until",
    "task_overdue_from",
)
REVIEW = models.Q(status__in=["needs_review", "failed"])
UNASSIGNED = models.Q(status="done", addressee_ids=[])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0024_letter_status"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                fields=["workspace", "id"],
                include=[*POLICY, *COUNTED],
                name="documents_document_policy",
            ),
        ),
        migrations.RemoveIndex(model_name="document", name="documents_document_review"),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                fields=["workspace", "created_at", "id"],
                include=POLICY,
                condition=REVIEW,
                name="documents_document_review",
            ),
        ),
        migrations.RemoveIndex(model_name="document", name="documents_document_unassigned"),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                fields=["workspace", "-created_at"],
                include=["id", *POLICY],
                condition=UNASSIGNED,
                name="documents_document_unassigned",
            ),
        ),
    ]

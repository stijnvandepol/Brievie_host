"""The trash: `Document.trashed_at`, `trashed_by` and `purge_started_at`, a check that a
purge only starts on a letter in the trash, and a partial index for the trash list and the
expiry query. Two history kinds (`trashed`, `restored`)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0014_document_has_automatic_link"),
        ("workspaces", "0007_visibility_functions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="purge_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="trashed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="document",
            name="trashed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="documentevent",
            name="kind",
            field=models.CharField(
                choices=[
                    ("label_added", "Label Added"),
                    ("label_removed", "Label Removed"),
                    ("addressee_added", "Addressee Added"),
                    ("addressee_removed", "Addressee Removed"),
                    ("confirmed", "Confirmed"),
                    ("task_completed", "Task Completed"),
                    ("task_reopened", "Task Reopened"),
                    ("reopened", "Reopened"),
                    ("made_private", "Made Private"),
                    ("made_public", "Made Public"),
                    ("share_added", "Share Added"),
                    ("share_removed", "Share Removed"),
                    ("access_granted", "Access Granted"),
                    ("emergency_opened", "Emergency Opened"),
                    ("private_skipped", "Private Skipped"),
                    ("trashed", "Trashed"),
                    ("restored", "Restored"),
                ],
                max_length=30,
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(("trashed_at__isnull", False)),
                fields=["workspace", "trashed_at"],
                name="documents_document_trash",
            ),
        ),
        migrations.AddConstraint(
            model_name="document",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("purge_started_at__isnull", True),
                    ("trashed_at__isnull", False),
                    _connector="OR",
                ),
                name="documents_document_purge_needs_trash",
            ),
        ),
    ]

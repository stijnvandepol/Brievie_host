"""Four history kinds of the archive: document_added, details_changed, retention_changed,
reminder_started (choices only)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0028_search_vector_title"),
    ]

    operations = [
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
                    ("merged", "Merged"),
                    ("duplicate_dismissed", "Duplicate Dismissed"),
                    ("task_moved", "Task Moved"),
                    ("task_assigned", "Task Assigned"),
                    ("task_unassigned", "Task Unassigned"),
                    ("task_deadline_set", "Task Deadline Set"),
                    ("task_waiting", "Task Waiting"),
                    ("task_waiting_ended", "Task Waiting Ended"),
                    ("letter_handled", "Letter Handled"),
                    ("letter_reopened", "Letter Reopened"),
                    ("document_added", "Document Added"),
                    ("details_changed", "Details Changed"),
                    ("retention_changed", "Retention Changed"),
                    ("reminder_started", "Reminder Started"),
                ],
                max_length=30,
            ),
        ),
    ]

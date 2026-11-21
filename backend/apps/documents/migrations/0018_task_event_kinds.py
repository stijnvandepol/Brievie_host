from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0017_duplicate_state_default"),
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
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="documentevent",
            name="origin",
            field=models.CharField(
                choices=[
                    ("user", "User"),
                    ("rule", "Rule"),
                    ("suggestion", "Suggestion"),
                    ("undo", "Undo"),
                    ("ai", "Ai"),
                    ("name_match", "Name Match"),
                    ("rule_autopilot", "Rule Autopilot"),
                    ("merge", "Merge"),
                    ("system", "System"),
                ],
                default="user",
                max_length=20,
            ),
        ),
    ]

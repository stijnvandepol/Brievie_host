from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0018_task_event_kinds"),
        ("workspaces", "0008_workspace_summary_language"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(("status__in", ["needs_review", "failed"])),
                fields=["workspace"],
                name="documents_document_review",
            ),
        ),
    ]

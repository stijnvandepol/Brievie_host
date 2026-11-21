"""`AccountNotice.workspace` (navigation spec S 8): a notice about one workspace is shown
only there. Notices from before stay account-wide (null)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0014_visibility_notices"),
        ("workspaces", "0010_unread_and_lingering"),
    ]

    operations = [
        migrations.AddField(
            model_name="accountnotice",
            name="workspace",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="workspaces.workspace",
            ),
        ),
    ]

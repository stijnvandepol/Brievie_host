from django.db import migrations

from apps.workspaces.rls import restrict_to_visible_documents


class Migration(migrations.Migration):
    dependencies = [
        ("rules", "0003_update_old_preset_rules"),
        ("documents", "0011_visibility"),
    ]

    operations = [restrict_to_visible_documents("rules_rulerun")]

"""`Document.has_automatic_link`: whether an automatic label or addressee link is on the
letter, stored so the inbox counts need no lookup in the link tables per letter
(DECISIONS 2026-11-20). Filled from the links per workspace, as the system. Reversible:
the reverse drops the column (it is derived)."""

from typing import Final

from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from apps.workspaces.rls import ACTOR_SETTING, SETTING_NAME

FILL: Final = (
    "UPDATE documents_document d SET has_automatic_link = true"
    " WHERE d.workspace_id = %s AND (EXISTS (SELECT 1 FROM documents_documentlabel l"
    " WHERE l.document_id = d.id AND l.automatic) OR EXISTS (SELECT 1"
    " FROM documents_documentaddressee a WHERE a.document_id = d.id AND a.automatic))"
)


def fill(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    workspaces = apps.get_model("workspaces", "Workspace")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, 'system', true)", [ACTOR_SETTING])
        for workspace_id in workspaces.objects.values_list("id", flat=True):
            cursor.execute("SELECT set_config(%s, %s, true)", [SETTING_NAME, str(workspace_id)])
            cursor.execute(FILL, [workspace_id])
        cursor.execute("SELECT set_config(%s, '', true)", [SETTING_NAME])
        cursor.execute("SELECT set_config(%s, '', true)", [ACTOR_SETTING])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0013_automatic_link_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="has_automatic_link",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]

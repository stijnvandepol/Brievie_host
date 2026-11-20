import uuid

import django.contrib.postgres.fields
import django.contrib.postgres.indexes
import django.db.models.deletion
from django.apps.registry import Apps
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from apps.workspaces.rls import (
    SETTING_NAME,
    document_visibility_policy,
    enable_rls,
    restrict_reader_rows,
    restrict_to_visible_documents,
)

FILL = (
    "UPDATE documents_document d SET addressee_ids = s.ids"
    " FROM (SELECT document_id, array_agg(person_id ORDER BY person_id) AS ids"
    " FROM documents_documentaddressee GROUP BY document_id) s"
    " WHERE s.document_id = d.id"
)

CHILD_TABLES = (
    "documents_documentpage",
    "documents_extraction",
    "documents_documentaddressee",
    "documents_documentlabel",
    "documents_documentevent",
    "documents_documentsuggestion",
    "documents_documentshare",
)


def fill_addressee_ids(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """S 4.9.2: every letter's `addressee_ids` from its addressee links, per workspace (the
    workspace policy binds the migrating owner too). Runs before the document policies
    exist, so the workspace setting is all it needs."""
    workspaces = apps.get_model("workspaces", "Workspace")
    with schema_editor.connection.cursor() as cursor:
        for workspace_id in workspaces.objects.values_list("id", flat=True):
            cursor.execute("SELECT set_config(%s, %s, true)", [SETTING_NAME, str(workspace_id)])
            cursor.execute(FILL)
        cursor.execute("SELECT set_config(%s, '', true)", [SETTING_NAME])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0011_autopilot_rule_labels"),
        ("workspaces", "0007_visibility_functions"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="visibility",
            field=models.CharField(
                choices=[("workspace", "Normaal"), ("restricted", "Privé")],
                default="workspace",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="addressee_ids",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.UUIDField(), blank=True, default=list, size=None
            ),
        ),
        migrations.AddField(
            model_name="documentevent",
            name="note",
            field=models.TextField(blank=True, default=""),
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
                ],
                max_length=30,
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["addressee_ids"], name="documents_document_addressees"
            ),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(("addressee_ids", []), ("status", "done")),
                fields=["workspace", "-created_at"],
                name="documents_document_unassigned",
            ),
        ),
        migrations.CreateModel(
            name="DocumentShare",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shares",
                        to="documents.document",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="workspaces.workspace"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("document", "user"), name="documents_documentshare_unique"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="DocumentReader",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "reason",
                    models.CharField(
                        choices=[
                            ("addressee", "Addressee"),
                            ("department", "Department"),
                            ("share", "Share"),
                            ("emergency", "Emergency"),
                        ],
                        max_length=20,
                    ),
                ),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="readers",
                        to="documents.document",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="workspaces.workspace"
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("document", "user", "reason"),
                        name="documents_documentreader_unique",
                    )
                ],
                "indexes": [
                    models.Index(fields=["user", "document"], name="documents_reader_user_document")
                ],
            },
        ),
        enable_rls("documents_documentshare"),
        enable_rls("documents_documentreader"),
        restrict_reader_rows(),
        migrations.RunPython(fill_addressee_ids, migrations.RunPython.noop),
        document_visibility_policy(),
        *(restrict_to_visible_documents(table) for table in CHILD_TABLES),
    ]

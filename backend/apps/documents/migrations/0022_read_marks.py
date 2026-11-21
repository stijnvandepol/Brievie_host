"""Navigation (spec S 5): `DocumentReadMark`, one row per member and letter they read,
under forced row-level security with the letter's policies and a restrictive own-row
policy (`read_mark_own`: a member reads and writes only their own marks, the system all);
`Document.addressee_opened_at` with the partial index of "Nog niet geopend".

Every confirmed letter with addressees counts as opened by an addressee from its last
change on (`updated_at`): no flood of "nog niet geopend" on the first day. Filled per
workspace, as the system (the document policies bind the migrating owner too)."""

import uuid
from typing import Final

import django.db.models.deletion
from django.apps.registry import Apps
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.rls import (
    READ_MARK_POLICY,
    enable_rls,
    own_rows_policy,
    restrict_to_visible_documents,
)

TABLE: Final = "documents_documentreadmark"

FILL: Final = (
    "UPDATE documents_document SET addressee_opened_at = updated_at"
    " WHERE workspace_id = %s AND status = 'done' AND addressee_ids <> '{}'"
    " AND addressee_opened_at IS NULL"
)


def fill(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    workspaces = apps.get_model("workspaces", "Workspace")
    for workspace_id in list(workspaces.objects.values_list("id", flat=True)):
        with (
            workspace_context(workspace_id, actor=SYSTEM),
            schema_editor.connection.cursor() as cursor,
        ):
            cursor.execute(FILL, [workspace_id])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0021_reader_managed"),
        ("workspaces", "0010_unread_and_lingering"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentReadMark",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("read_at", models.DateTimeField()),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="read_marks",
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
                "indexes": [
                    models.Index(fields=["user", "document"], name="documents_readmark_user")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("document", "user"), name="documents_readmark_unique"
                    )
                ],
            },
        ),
        enable_rls(TABLE),
        restrict_to_visible_documents(TABLE),
        own_rows_policy(TABLE, READ_MARK_POLICY),
        migrations.AddField(
            model_name="document",
            name="addressee_opened_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="document",
            index=models.Index(
                condition=models.Q(("addressee_opened_at__isnull", True), ("status", "done"))
                & ~models.Q(addressee_ids=[])
                & models.Q(trashed_at__isnull=True),
                fields=["workspace", "confirmed_at"],
                name="documents_document_unopened",
            ),
        ),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]

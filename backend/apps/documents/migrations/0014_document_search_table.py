"""The search vector moves from the letter table into `documents_documentsearch`.

Stored beside the text it made every letter row about twice as wide, and the lists and
inbox counts scan that table (DECISIONS 2026-11-20). The new table is a workspace content
table with the letter's own visibility (`restrict_to_visible_documents`), deleted with its
letter like its other rows. A trigger keeps it, as the generated column did: built by
PostgreSQL from the stored, already BSN-masked sender, summary and text. Existing letters
are filled in batches per workspace. Reversible: the reverse restores the generated
column and its index and drops the table, the trigger and the function.
"""

import uuid
from typing import Final

import django.contrib.postgres.indexes
import django.contrib.postgres.search
import django.db.models.deletion
from django.apps.registry import Apps
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from apps.workspaces.rls import (
    ACTOR_SETTING,
    SETTING_NAME,
    enable_rls,
    restrict_to_visible_documents,
)

TABLE: Final = "documents_documentsearch"
BATCH: Final = 500


def vector_sql(row: str) -> str:
    """The vector of the letter `row` (NEW in the trigger, a table alias in the fill): the
    same expression the generated column had (sender A, summary B, text C, Dutch)."""
    return (
        f"setweight(to_tsvector('dutch'::regconfig, COALESCE({row}.sender, '')), 'A')"
        f" || setweight(to_tsvector('dutch'::regconfig, COALESCE({row}.summary, '')), 'B')"
        f" || setweight(to_tsvector('dutch'::regconfig, COALESCE({row}.ocr_text, '')), 'C')"
    )


# SECURITY INVOKER (the default): the row is written with the policies of whoever stores
# the letter. A plain INSERT checks only the workspace policy (no ON CONFLICT: that would
# also read the row through the letter's policy). A change of a letter's text is made by
# someone who sees the letter (or the system), so its search row is visible to them too.
FUNCTION_SQL: Final = (
    f"""CREATE FUNCTION public.brievie_document_search_insert() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog
AS $$
BEGIN
  INSERT INTO {TABLE} (id, created_at, updated_at, workspace_id, document_id, search_vector)
  VALUES (gen_random_uuid(), now(), now(), NEW.workspace_id, NEW.id, {vector_sql("NEW")});
  RETURN NULL;
END
$$""",  # noqa: S608 - constants only
    f"""CREATE FUNCTION public.brievie_document_search_update() RETURNS trigger
LANGUAGE plpgsql SET search_path = public, pg_catalog
AS $$
BEGIN
  UPDATE {TABLE} SET search_vector = {vector_sql("NEW")}, updated_at = now()
  WHERE document_id = NEW.id;
  RETURN NULL;
END
$$""",  # noqa: S608 - constants only
)

TRIGGER_SQL: Final = (
    "CREATE TRIGGER documents_document_search_insert AFTER INSERT ON documents_document"
    " FOR EACH ROW EXECUTE FUNCTION public.brievie_document_search_insert()",
    "CREATE TRIGGER documents_document_search_update"
    " AFTER UPDATE OF sender, summary, ocr_text ON documents_document FOR EACH ROW"
    " WHEN (OLD.sender IS DISTINCT FROM NEW.sender OR OLD.summary IS DISTINCT FROM NEW.summary"
    " OR OLD.ocr_text IS DISTINCT FROM NEW.ocr_text)"
    " EXECUTE FUNCTION public.brievie_document_search_update()",
)

DROP_SQL: Final = (
    "DROP TRIGGER documents_document_search_update ON documents_document",
    "DROP TRIGGER documents_document_search_insert ON documents_document",
    "DROP FUNCTION public.brievie_document_search_update()",
    "DROP FUNCTION public.brievie_document_search_insert()",
)

FILL: Final = (
    f"INSERT INTO {TABLE} (id, created_at, updated_at, workspace_id, document_id, search_vector)"  # noqa: S608 - constants only
    f" SELECT gen_random_uuid(), now(), now(), d.workspace_id, d.id, {vector_sql('d')}"
    " FROM documents_document d WHERE d.id = ANY(%s::uuid[])"
    " ON CONFLICT (document_id) DO NOTHING"
)


def fill(apps: Apps, schema_editor: BaseDatabaseSchemaEditor) -> None:
    """A search row for every existing letter, per workspace, `BATCH` letters at a time,
    as the system (the document policies bind the migrating owner too)."""
    workspaces = apps.get_model("workspaces", "Workspace")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, 'system', true)", [ACTOR_SETTING])
        for workspace_id in workspaces.objects.values_list("id", flat=True):
            cursor.execute("SELECT set_config(%s, %s, true)", [SETTING_NAME, str(workspace_id)])
            last = uuid.UUID(int=0)
            while True:
                cursor.execute(
                    "SELECT id FROM documents_document WHERE workspace_id = %s AND id > %s"
                    " ORDER BY id LIMIT %s",
                    [workspace_id, last, BATCH],
                )
                ids = [row[0] for row in cursor.fetchall()]
                if not ids:
                    break
                cursor.execute(FILL, [ids])
                last = ids[-1]
        cursor.execute("SELECT set_config(%s, '', true)", [SETTING_NAME])
        cursor.execute("SELECT set_config(%s, '', true)", [ACTOR_SETTING])


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0013_automatic_link_indexes"),
        ("workspaces", "0007_visibility_functions"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentSearch",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("search_vector", django.contrib.postgres.search.SearchVectorField()),
                (
                    "document",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="search",
                        to="documents.document",
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
                    django.contrib.postgres.indexes.GinIndex(
                        fields=["search_vector"], name="documents_docsearch_vector"
                    )
                ],
            },
        ),
        enable_rls(TABLE),
        restrict_to_visible_documents(TABLE),
        migrations.RunSQL(sql=[*FUNCTION_SQL, *TRIGGER_SQL], reverse_sql=list(DROP_SQL)),
        migrations.RunPython(fill, migrations.RunPython.noop),
        migrations.RemoveIndex(model_name="document", name="documents_document_search"),
        migrations.RemoveField(model_name="document", name="search_vector"),
    ]

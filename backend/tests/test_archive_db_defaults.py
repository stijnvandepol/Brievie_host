"""The archive's new not-null columns have a database default where the model has one: a
raw INSERT that leaves them out (a seed, a restore script, the perf data) keeps working
(archive plan Task 1)."""

import uuid

from django.db import connection

from apps.documents.models import Document
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.visibility_support import as_actor


def _column_default(table: str, column: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = %s AND column_name = %s",
            [table, column],
        )
        row = cursor.fetchone()
    assert row is not None
    value = row[0]
    return None if value is None else str(value)


def test_db_defaults_for_new_columns() -> None:
    assert _column_default("documents_document", "kind") == "'letter'::character varying"
    assert _column_default("documents_document", "title") == "''::character varying"
    assert _column_default("documents_document", "remind_days") == "30"
    assert _column_default("documents_document", "expires_on") is None
    assert _column_default("documents_document", "retain_until") is None


def test_raw_insert_without_the_new_columns_still_works(ws_a: Workspace) -> None:
    document_id = uuid.uuid4()
    with as_actor(ws_a, SYSTEM):
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO documents_document (id, workspace_id, created_at, updated_at,"
                " client_id, source, status, failure_code, page_count, ocr_text, sender,"
                " document_type, summary, summary_by_ai, confirmed_by_ai, has_automatic_link,"
                " visibility, addressee_ids)"
                " VALUES (%s, %s, now(), now(), %s, 'scan', 'done', '', 1, '', '', '', '',"
                " false, false, false, 'workspace', '{}')",
                [document_id, ws_a.id, uuid.uuid4()],
            )
        stored = Document.objects.get(id=document_id)
    assert (stored.kind, stored.title, stored.remind_days) == ("letter", "", 30)

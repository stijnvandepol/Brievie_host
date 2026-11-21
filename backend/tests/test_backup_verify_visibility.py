"""The back-up check knows the visibility functions and policies (plan Task 34, PB T21).

Each case breaks the test database inside the test's own transaction (DDL is
transactional in PostgreSQL), so the rollback at the end restores it."""

import psycopg
import pytest
from django.db import connection

from apps.backup.verify import privacy_problems

pytestmark = pytest.mark.django_db

FUNCTIONS = "De database mist de functies voor wie welke post ziet."


def _conn() -> psycopg.Connection[tuple[object, ...]]:
    connection.ensure_connection()
    raw = connection.connection
    assert isinstance(raw, psycopg.Connection)
    return raw


def _run(sql: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)


def _table(table: str) -> str:
    return f"De database mist de regels voor wie welke post ziet in tabel {table}."


def test_restore_with_the_policies_passes() -> None:
    assert privacy_problems(_conn()) == []


def test_restore_without_a_helper_function_fails() -> None:
    _run("DROP FUNCTION public.brievie_my_post_ids(uuid) CASCADE")
    problems = privacy_problems(_conn())
    assert FUNCTIONS in problems
    assert _table("documents_document") in problems


def test_restore_without_a_visibility_policy_fails() -> None:
    _run('DROP POLICY document_visibility_delete ON "documents_documentlabel"')
    assert privacy_problems(_conn()) == [_table("documents_documentlabel")]


def test_the_task_table_is_checked_like_the_child_tables() -> None:
    _run('DROP POLICY document_visibility ON "tasks_task"')
    assert privacy_problems(_conn()) == [_table("tasks_task")]


def test_the_reader_table_needs_its_own_policy() -> None:
    _run('DROP POLICY reader_scope ON "documents_documentreader"')
    assert privacy_problems(_conn()) == [_table("documents_documentreader")]


def test_the_read_mark_table_needs_its_own_row_policy() -> None:
    _run('DROP POLICY read_mark_own ON "documents_documentreadmark"')
    assert privacy_problems(_conn()) == [_table("documents_documentreadmark")]


def test_a_back_up_from_before_the_functions_is_not_checked_for_them() -> None:
    _run(
        "DELETE FROM django_migrations"
        " WHERE app = 'workspaces' AND name = '0007_visibility_functions'"
    )
    _run("DROP FUNCTION public.brievie_my_post_ids(uuid) CASCADE")
    assert privacy_problems(_conn()) == []

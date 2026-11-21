"""The new duplicate columns have a database default where the model has one: a raw INSERT
that leaves them out (a seed, a restore script, the perf data) keeps working."""

from django.db import connection


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


def test_a_duplicate_pair_state_defaults_to_open_in_the_database() -> None:
    assert _column_default("documents_duplicatecandidate", "state") == "'open'::character varying"


def test_a_document_event_related_document_is_nullable_without_default() -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_name = 'documents_documentevent' AND column_name = 'related_document_id'"
        )
        row = cursor.fetchone()
    assert row == ("YES",)

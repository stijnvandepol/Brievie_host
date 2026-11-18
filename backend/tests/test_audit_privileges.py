"""The audit tables are append-only for the application's database role (D 11).

The suite runs as the schema owner, which may change anything; these tests ask
PostgreSQL about the privileges of `brievie_app` itself, so a forgotten REVOKE fails.
"""

import importlib

import pytest
from django.db import connection

APP_ROLE = "brievie_app"
APPEND_ONLY_TABLES = [
    "audit_workspaceauditevent",
    "audit_serverauditevent",
    "pipeline_aiconsentevent",
]


def has_table_privilege(table: str, privilege: str) -> bool:
    with connection.cursor() as c:
        c.execute("SELECT has_table_privilege(%s, %s, %s)", [APP_ROLE, table, privilege])
        row = c.fetchone()
    assert row is not None
    return bool(row[0])


def test_app_role_exists() -> None:
    with connection.cursor() as c:
        c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [APP_ROLE])
        assert c.fetchone() == (1,)


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_app_role_may_insert_and_read_audit_rows_but_not_change_them(table: str) -> None:
    assert has_table_privilege(table, "SELECT")
    assert has_table_privilege(table, "INSERT")
    assert not has_table_privilege(table, "UPDATE")
    assert not has_table_privilege(table, "DELETE")
    assert not has_table_privilege(table, "TRUNCATE")


def test_audit_tables_have_no_foreign_keys() -> None:
    with connection.cursor() as c:
        c.execute(
            """
            SELECT conrelid::regclass::text, conname FROM pg_constraint
            WHERE contype = 'f'
              AND conrelid IN ('audit_workspaceauditevent'::regclass,
                               'audit_serverauditevent'::regclass)
            """
        )
        assert c.fetchall() == []


def test_the_migration_revokes_what_default_privileges_grant() -> None:
    """Production's default privileges give the app role UPDATE and DELETE on every new
    table; the test database has none. Grant them here, as production would, and run
    the migration's statement again: it must take them away."""
    migration = importlib.import_module("apps.audit.migrations.0001_initial")
    with connection.cursor() as c:
        for table in APPEND_ONLY_TABLES:
            c.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON "{table}" TO {APP_ROLE}')
        assert has_table_privilege(APPEND_ONLY_TABLES[0], "UPDATE")
        c.execute(migration.APPEND_ONLY)
    for table in APPEND_ONLY_TABLES:
        for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
            assert not has_table_privilege(table, privilege), (table, privilege)

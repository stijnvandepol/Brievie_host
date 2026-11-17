"""The role brievie_backup reads every workspace past forced RLS, and nothing else."""

from pathlib import Path

import psycopg
import pytest
from django.db import ProgrammingError, connection, transaction
from psycopg import errors as pg_errors

from apps.documents.models import Folder
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
COUNT_WORKSPACES = "SELECT count(DISTINCT workspace_id) FROM documents_folder"


@pytest.mark.django_db(transaction=True)
def test_backup_role_reads_rows_of_two_workspaces(
    ws_a: Workspace, ws_b: Workspace, backup_conninfo: str
) -> None:
    for workspace in (ws_a, ws_b):
        with workspace_context(workspace.id):
            Folder.objects.create(workspace=workspace, name="Back-up")

    with psycopg.connect(backup_conninfo) as conn:
        conn.execute("SET row_security = off")
        row = conn.execute(COUNT_WORKSPACES).fetchone()

    assert row == (2,)


def _count_workspaces_without_row_security() -> None:
    with transaction.atomic(), connection.cursor() as c:
        c.execute("SET LOCAL row_security = off")
        c.execute(COUNT_WORKSPACES)


@pytest.mark.django_db(transaction=True)
def test_owner_cannot_read_with_row_security_off(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id):
        Folder.objects.create(workspace=ws_a, name="Back-up")

    with pytest.raises(ProgrammingError) as raised:
        _count_workspaces_without_row_security()

    assert isinstance(raised.value.__cause__, pg_errors.InsufficientPrivilege)


@pytest.mark.django_db(transaction=True)
def test_backup_role_cannot_write(ws_a: Workspace, backup_conninfo: str) -> None:
    with psycopg.connect(backup_conninfo) as conn, pytest.raises(pg_errors.InsufficientPrivilege):
        conn.execute(
            "INSERT INTO documents_folder (id, created_at, updated_at, workspace_id, name, path)"
            " VALUES (gen_random_uuid(), now(), now(), %s, 'x', '/x')",
            [ws_a.id],
        )


@pytest.mark.django_db(transaction=True)
def test_backup_role_attributes(backup_conninfo: str) -> None:
    with psycopg.connect(backup_conninfo) as conn:
        row = conn.execute(
            "SELECT rolbypassrls, rolsuper, rolcreaterole, rolcreatedb, rolinherit,"
            " pg_has_role(current_user, 'pg_read_all_data', 'member'),"
            " has_database_privilege(current_user, 'brievie', 'CONNECT')"
            " FROM pg_roles WHERE rolname = current_user"
        ).fetchone()

    # CONNECT is checked on the real database `brievie`, where backup-role.psql grants
    # it (REVOKE ALL FROM PUBLIC there). The test database is a fresh one with the
    # default CONNECT for PUBLIC, so it would prove nothing.
    assert row == (True, False, False, False, True, True, True)


def test_ensure_backup_role_is_idempotent() -> None:
    sql = (DEPLOY / "postgres-init" / "backup-role.psql").read_text()
    roles = (DEPLOY / "postgres-init" / "01-roles.sh").read_text()

    # Creating the role is guarded, so the script can run again on an existing server;
    # ALTER ROLE then sets the attributes and the password every time.
    assert "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'brievie_backup')\\gexec" in sql
    assert "ALTER ROLE brievie_backup LOGIN BYPASSRLS NOSUPERUSER" in sql
    assert "${BASH_SOURCE[0]}" in roles
    assert "backup-role.psql" in roles

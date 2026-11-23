"""Roles and database of a compose install against the CI PostgreSQL, with scratch names.

The statements are compared with the SQL files the wrapper and the dev stack still use,
so the two ways to make a server cannot drift apart. Nothing here touches the real
`brievie` roles or database: every real-server test makes and drops its own names.
"""

import os
import re
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import TupleRow

from apps.installer import database
from apps.installer.database import (
    Connect,
    DatabaseSetupError,
    Names,
    Passwords,
    ensure_roles_and_database,
)

POSTGRES_INIT = Path(__file__).resolve().parents[2] / "deploy" / "postgres-init"
PASSWORDS = Passwords(owner="o'w\"n\\er pw", app="app-pw-0123", backup="backup-pw-0123")


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace('"', "")).strip()


def _statements(text: str) -> list[str]:
    lines = [line.split("--")[0] for line in text.splitlines() if not line.startswith("\\")]
    return [_normal(part) for part in " ".join(lines).split(";") if part.strip()]


# --- the statements agree with the SQL files (no server needed) ---


def _wrapper_statements() -> list[str]:
    files = [
        (POSTGRES_INIT / "database.psql").read_text(),
        (POSTGRES_INIT / "backup-role.psql").read_text(),
    ]
    return [statement for text in files for statement in _statements(text)]


def _python_statements(names: Names) -> list[str]:
    composed = [*database.server_statements(names), *database.schema_statements(names)]
    return [_normal(item.as_string()) for item in composed]


def test_every_grant_of_the_sql_files_is_made_here() -> None:
    wanted = {
        statement
        for statement in _wrapper_statements()
        if statement.startswith(("REVOKE", "GRANT", "ALTER DEFAULT PRIVILEGES"))
    }
    made = set(_python_statements(Names()))
    assert wanted
    assert wanted == made


def test_role_attributes_equal_those_of_the_sql_files() -> None:
    attributes = {_normal(v) for v in database.role_attributes(Names()).values()}
    script = (POSTGRES_INIT / "01-roles.sh").read_text()
    backup = (POSTGRES_INIT / "backup-role.psql").read_text()
    found = set()
    for text in (script, backup):
        for match in re.finditer(
            r"(?:CREATE|ALTER) ROLE (brievie_\w+)\s+([A-Z ]+?)\s+PASSWORD", text
        ):
            found.add(_normal(match.group(2)))
    assert found == attributes


def test_the_database_is_made_with_the_owner_role_like_the_sql_file() -> None:
    wanted = [s for s in _wrapper_statements() if s.startswith("CREATE DATABASE")]
    assert wanted == ["CREATE DATABASE brievie OWNER brievie_owner"]


# --- a real server ---


@pytest.fixture
def connect() -> Connect:
    if not os.environ.get("PGPASSWORD"):
        pytest.skip("needs the PostgreSQL superuser of the CI service (PGPASSWORD)")
    user = os.environ.get("POSTGRES_USER", "postgres")

    def factory(dbname: str) -> psycopg.Connection[TupleRow]:
        return psycopg.connect(dbname=dbname, user=user, autocommit=True)

    return factory


@pytest.fixture
def scratch(connect: Connect) -> Iterator[Names]:
    tag = secrets.token_hex(4)
    names = Names(
        owner=f"scratch_owner_{tag}",
        app=f"scratch_app_{tag}",
        backup=f"scratch_backup_{tag}",
        database=f"scratch_db_{tag}",
    )
    yield names
    assert names.database.startswith("scratch_db_")
    with connect("postgres") as server:
        server.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(names.database)
            )
        )
        for role in (names.owner, names.app, names.backup):
            server.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@dataclass(frozen=True)
class Facts:
    roles: list[TupleRow]
    database: TupleRow | None
    member: TupleRow | None
    schema: TupleRow | None
    defaults: list[TupleRow]


def _facts(connect: Connect, names: Names) -> Facts:
    with connect("postgres") as server:
        roles = server.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls,"
            " rolinherit, rolpassword IS NOT NULL FROM pg_roles r"
            " LEFT JOIN pg_authid a USING (rolname) WHERE rolname = ANY(%s) ORDER BY rolname",
            [[names.owner, names.app, names.backup]],
        ).fetchall()
        row = server.execute(
            "SELECT pg_get_userbyid(datdba), datacl::text FROM pg_database WHERE datname = %s",
            [names.database],
        ).fetchone()
        member = server.execute(
            "SELECT pg_has_role(%s, 'pg_read_all_data', 'member')", [names.backup]
        ).fetchone()
    with connect(names.database) as inside:
        schema = inside.execute(
            "SELECT nspacl::text FROM pg_namespace WHERE nspname = 'public'"
        ).fetchone()
        defaults = inside.execute(
            "SELECT pg_get_userbyid(defaclrole), defaclobjtype, defaclacl::text"
            " FROM pg_default_acl ORDER BY 2"
        ).fetchall()
    return Facts(roles, row, member, schema, defaults)


def test_an_empty_server_gets_the_roles_and_the_database(connect: Connect, scratch: Names) -> None:
    ensure_roles_and_database(connect, PASSWORDS, scratch)
    facts = _facts(connect, scratch)
    by_name = {row[0]: row[1:] for row in facts.roles}
    # login, super, createdb, createrole, bypassrls, inherit, has password
    assert by_name[scratch.owner] == (True, False, True, False, False, True, True)
    assert by_name[scratch.app] == (True, False, False, False, False, True, True)
    assert by_name[scratch.backup] == (True, False, False, False, True, True, True)
    assert facts.database is not None
    owner, acl = facts.database
    assert owner == scratch.owner
    assert f"{scratch.app}=c/{scratch.owner}" in acl
    assert f"{scratch.backup}=c/{scratch.owner}" in acl
    assert not any(entry.startswith("=") for entry in acl.strip("{}").split(","))
    assert facts.member == (True,)


def test_the_app_role_cannot_create_in_the_schema(connect: Connect, scratch: Names) -> None:
    ensure_roles_and_database(connect, PASSWORDS, scratch)
    with connect(scratch.database) as inside:
        row = inside.execute(
            "SELECT has_schema_privilege(%s, 'public', 'CREATE'),"
            " has_schema_privilege(%s, 'public', 'USAGE'),"
            " has_schema_privilege(%s, 'public', 'CREATE')",
            [scratch.app, scratch.app, scratch.owner],
        ).fetchone()
    assert row == (False, True, True)


def test_a_second_run_changes_nothing(connect: Connect, scratch: Names) -> None:
    ensure_roles_and_database(connect, PASSWORDS, scratch)
    first = _facts(connect, scratch)
    ensure_roles_and_database(connect, PASSWORDS, scratch)
    assert _facts(connect, scratch) == first


def test_a_changed_password_reaches_the_database(connect: Connect, scratch: Names) -> None:
    ensure_roles_and_database(connect, PASSWORDS, scratch)
    with connect("postgres") as server:
        before = server.execute(
            "SELECT rolpassword FROM pg_authid WHERE rolname = %s", [scratch.app]
        ).fetchone()
    ensure_roles_and_database(connect, Passwords("o", "another-app-pw", "b"), scratch)
    with connect("postgres") as server:
        after = server.execute(
            "SELECT rolpassword FROM pg_authid WHERE rolname = %s", [scratch.app]
        ).fetchone()
    assert before != after


# --- a failure never shows a password ---


class _FailingConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self) -> "_FailingConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: object, params: object = None) -> "_FailingConnection":
        text = statement.as_string() if isinstance(statement, sql.Composable) else str(statement)
        self.statements.append(text)
        if "PASSWORD" in text:
            raise psycopg.errors.SyntaxError(f"syntax error at: {text}")
        return self

    def fetchone(self) -> None:
        return None


def test_a_failing_statement_names_only_the_step(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    fake = _FailingConnection()
    with pytest.raises(DatabaseSetupError) as caught:
        ensure_roles_and_database(lambda _name: fake, PASSWORDS)  # type: ignore[arg-type,return-value]
    assert caught.value.step == "alter_role"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    haystack = f"{caught.value} {capsys.readouterr()} {caplog.text}"
    assert "app-pw-0123" not in haystack
    assert "o'w" not in haystack


def test_the_session_is_quiet_before_a_password_is_sent() -> None:
    fake = _FailingConnection()
    with pytest.raises(DatabaseSetupError):
        ensure_roles_and_database(lambda _name: fake, PASSWORDS)  # type: ignore[arg-type,return-value]
    quiet = [i for i, text in enumerate(fake.statements) if "log_min_error_statement" in text]
    password = [i for i, text in enumerate(fake.statements) if "PASSWORD" in text]
    assert quiet
    assert quiet[0] < password[0]

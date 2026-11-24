"""The roles and the database of a compose install, made as the PostgreSQL superuser.

The effect of `deploy/postgres-init/01-roles.sh`, `database.psql` and `backup-role.psql`
together (the wrapper and the dev stack still use those files), but idempotent: a role is
made when it is missing and its attributes and password are set every time, so a changed
vault value reaches the database and an existing wrapper database passes through without
a statement that changes data. A test compares the statements here with those files.

PostgreSQL cannot bind a parameter in `ALTER ROLE ... PASSWORD`, so the password is a
quoted literal in the statement. The session therefore first switches off every server
log that would print a statement, and an error is re-raised without psycopg's text.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import psycopg
from psycopg import sql
from psycopg.rows import TupleRow

Connection = psycopg.Connection[TupleRow]
# database name -> an autocommit connection as the superuser.
Connect = Callable[[str], Connection]
Statement = sql.SQL | sql.Composed


class DatabaseSetupError(Exception):
    """A statement failed. Carries no SQL and no value: only which step."""

    def __init__(self, step: str) -> None:
        super().__init__(f"database setup failed at {step}")
        self.step = step


@dataclass(frozen=True)
class Names:
    owner: str = "brievie_owner"
    app: str = "brievie_app"
    backup: str = "brievie_backup"
    database: str = "brievie"


@dataclass(frozen=True)
class Passwords:
    owner: str = field(repr=False)
    app: str = field(repr=False)
    backup: str = field(repr=False)


def _ident(name: str) -> sql.Identifier:
    return sql.Identifier(name)


def role_attributes(names: Names) -> dict[str, str]:
    """The attribute list of each role, as `ALTER ROLE` takes it."""
    return {
        names.owner: "LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOBYPASSRLS",
        names.app: "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS",
        names.backup: "LOGIN BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT",
    }


def server_statements(names: Names) -> list[Statement]:
    """After the roles exist, in the `postgres` database: what database.psql and
    backup-role.psql grant at the database level."""
    database = _ident(names.database)
    return [
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, _ident(names.app)),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, _ident(names.backup)),
        sql.SQL("GRANT pg_read_all_data TO {}").format(_ident(names.backup)),
    ]


def schema_statements(names: Names) -> list[Statement]:
    """Inside the application database: the schema grants of database.psql."""
    owner, app = _ident(names.owner), _ident(names.app)
    return [
        sql.SQL("REVOKE CREATE ON SCHEMA public FROM PUBLIC"),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(app),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(owner, app),
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO {}"
        ).format(owner, app),
    ]


def _execute(connection: Connection, statement: Statement, step: str) -> None:
    try:
        connection.execute(statement)
    except psycopg.Error:
        # psycopg's message can hold the statement, and so the password: say only the step.
        raise DatabaseSetupError(step) from None


def _quiet(connection: Connection) -> None:
    for statement in ("SET log_min_error_statement = panic", "SET log_statement = 'none'"):
        _execute(connection, sql.SQL(statement), "quiet")


def _role_exists(connection: Connection, name: str) -> bool:
    row = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [name]).fetchone()
    return row is not None


def _database_exists(connection: Connection, name: str) -> bool:
    row = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", [name]).fetchone()
    return row is not None


def ensure_roles_and_database(
    connect: Connect, passwords: Passwords, names: Names = Names()
) -> None:
    """Roles and database as the wrapper's scripts make them; safe to run at every start."""
    secrets = {
        names.owner: passwords.owner,
        names.app: passwords.app,
        names.backup: passwords.backup,
    }
    attributes = role_attributes(names)
    try:
        with connect("postgres") as server:
            _quiet(server)
            for role in (names.owner, names.app, names.backup):
                if not _role_exists(server, role):
                    _execute(server, sql.SQL("CREATE ROLE {}").format(_ident(role)), "create_role")
                _execute(
                    server,
                    sql.SQL("ALTER ROLE {} {} PASSWORD {}").format(
                        _ident(role), sql.SQL(attributes[role]), sql.Literal(secrets[role])
                    ),
                    "alter_role",
                )
            if not _database_exists(server, names.database):
                _execute(
                    server,
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        _ident(names.database), _ident(names.owner)
                    ),
                    "create_database",
                )
            for statement in server_statements(names):
                _execute(server, statement, "grant_database")
        with connect(names.database) as database:
            for statement in schema_statements(names):
                _execute(database, statement, "grant_schema")
    except psycopg.Error:
        raise DatabaseSetupError("connect") from None

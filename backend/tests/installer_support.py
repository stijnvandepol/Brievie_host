"""Fixtures for the installer tests that need a real PostgreSQL (the CI service).

Every test makes its own `scratch_*` roles and database and drops them afterwards; the
real `brievie` ones are never touched. Without the service's superuser password
(PGPASSWORD) these tests skip.
"""

import os
import secrets
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import TupleRow

from apps.installer.database import Connect, Names, Passwords

PASSWORDS = Passwords(owner="owner-pw-0123456789", app="app-pw-0123456789", backup="backup-pw-0123")


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


def connect_as(names: Names, role: str, password: str) -> psycopg.Connection[TupleRow]:
    """A login as one of the scratch roles (what a client of that role would open)."""
    return psycopg.connect(
        dbname=names.database,
        user=role,
        password=password,
        host=os.environ.get("PGHOST", "localhost"),
        autocommit=True,
    )

import logging
import stat
from pathlib import Path

import psycopg
import pytest
from django.conf import settings
from django.db import connection, transaction
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pytest_django import Settings

from apps.backup import database
from apps.backup.database import (
    libpq_env,
    pg_dump_major,
    require_backup_role,
    require_client_for,
)
from apps.backup.errors import BackupError
from apps.backup.locks import BACKUP_LOCK_KEY, backup_lock


def _owner_conninfo() -> str:
    owner = settings.DATABASE_OWNER
    assert owner is not None
    return make_conninfo(
        dbname=str(connection.settings_dict["NAME"]),
        user=str(owner["USER"]),
        password=str(owner["PASSWORD"]),
        host=str(owner["HOST"]),
        port=str(owner["PORT"]),
    )


def _try_lock_elsewhere() -> bool:
    with psycopg.connect(_owner_conninfo(), autocommit=True) as other:
        row = other.execute("SELECT pg_try_advisory_lock(%s)", [BACKUP_LOCK_KEY]).fetchone()
        assert row is not None
        return bool(row[0])


@pytest.mark.django_db(transaction=True)
def test_lock_is_exclusive_across_connections() -> None:
    with backup_lock():
        assert _try_lock_elsewhere() is False
    # Released afterwards.
    assert _try_lock_elsewhere() is True


@pytest.mark.django_db(transaction=True)
def test_lock_taken_elsewhere_raises_busy() -> None:
    with psycopg.connect(_owner_conninfo(), autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", [BACKUP_LOCK_KEY])
        with pytest.raises(BackupError) as caught, backup_lock():
            pass
        assert caught.value.code == "busy"


def test_lock_refuses_an_open_transaction() -> None:
    with transaction.atomic(), pytest.raises(RuntimeError), backup_lock():
        pass


@pytest.mark.django_db(transaction=True)
def test_require_backup_role_accepts_the_backup_role(backup_conninfo: str) -> None:
    require_backup_role(backup_conninfo)


@pytest.mark.django_db(transaction=True)
def test_require_backup_role_refuses_the_owner() -> None:
    with pytest.raises(BackupError) as caught:
        require_backup_role(_owner_conninfo())
    assert caught.value.code == "backup_role_missing"


def test_require_backup_role_refuses_a_failed_login(
    backup_conninfo: str, caplog: pytest.LogCaptureFixture
) -> None:
    password = str(conninfo_to_dict(backup_conninfo)["password"])
    bad = make_conninfo(backup_conninfo, password="not-the-password-xyz")
    with caplog.at_level(logging.DEBUG), pytest.raises(BackupError) as caught:
        require_backup_role(bad)
    assert caught.value.code == "backup_role_missing"
    for text in (str(caught.value), repr(caught.value), caught.value.message, caplog.text):
        assert "not-the-password-xyz" not in text
        assert password not in text
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_libpq_env_carries_the_password_not_argv(backup_conninfo: str) -> None:
    parts = conninfo_to_dict(backup_conninfo)
    env = libpq_env(backup_conninfo)
    assert env["PGPASSWORD"] == parts["password"]
    assert env["PGUSER"] == parts["user"]
    assert env["PGHOST"] == parts["host"]
    assert env["PGPORT"] == str(parts["port"])
    assert env["PGDATABASE"] == parts["dbname"]


def test_backup_conninfo_uses_the_settings_and_a_given_database() -> None:
    info = conninfo_to_dict(database.backup_conninfo(dbname="other"))
    assert info["dbname"] == "other"
    assert info["user"] == "brievie_backup"


def test_conninfo_without_settings_is_refused(settings: Settings) -> None:
    settings.DATABASE_BACKUP = None
    settings.DATABASE_SUPERUSER = None
    with pytest.raises(BackupError) as backup:
        database.backup_conninfo()
    assert backup.value.code == "backup_role_missing"
    with pytest.raises(BackupError) as superuser:
        database.superuser_conninfo()
    assert superuser.value.code == "restore_failed"


def test_pg_dump_major_parses_the_version(tmp_path: Path) -> None:
    fake = tmp_path / "pg_dump"
    fake.write_text('#!/bin/sh\necho "pg_dump (PostgreSQL) 18.1 (Debian 18.1-1.pgdg13+1)"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    assert pg_dump_major(str(fake)) == 18


def test_client_older_than_server_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "server_major", lambda conninfo: 18)
    monkeypatch.setattr(database, "pg_dump_major", lambda binary="pg_dump": 17)
    with pytest.raises(BackupError) as caught:
        require_client_for("host=x")
    assert caught.value.code == "pg_client_too_old"
    monkeypatch.setattr(database, "pg_dump_major", lambda binary="pg_dump": 18)
    require_client_for("host=x")


@pytest.mark.django_db(transaction=True)
def test_server_major_reads_the_server(backup_conninfo: str) -> None:
    assert database.server_major(backup_conninfo) >= 14

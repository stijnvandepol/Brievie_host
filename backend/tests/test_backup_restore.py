"""Restore, part 2: the database, the files, and closing the runs the dump brought (Task 10)."""

import json
import os
import stat
import subprocess
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from pytest_django import Settings

from apps.backup import restic, services
from apps.backup.errors import BackupError
from apps.backup.locks import BACKUP_LOCK_KEY, backup_lock, database_lock
from apps.backup.models import BackupRun
from apps.backup.restore import (
    PG_RESTORE_COMMAND,
    FilesRestored,
    close_interrupted_runs,
    restore_database,
    restore_files,
)
from tests.backup_support import FakeBinary, Recorder

T0 = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)
SUPERUSER_PASSWORD = "superuser-secret-pw-0123"
BACKUP_PASSWORD = "p" * 40  # configured_folder's
AUTHORITY_FILES = ("root.crt", "root.key", "intermediate.crt", "intermediate.key")


def snapshot(snapshot_id: str, when: datetime, run_id: UUID, kind: str) -> dict[str, object]:
    return {
        "id": snapshot_id,
        "time": when.isoformat(),
        "tags": [f"run:{run_id}", f"kind:{kind}"],
        "paths": ["/brievie.pgdump" if kind == "db" else "/data/documents"],
    }


def one_run(fake_restic: FakeBinary) -> UUID:
    run_id = uuid4()
    fake_restic.respond(
        ("snapshots", "--no-lock", "--json", "--tag", "kind:db"),
        stdout=json.dumps([snapshot("d0000001", T0, run_id, "db")]).encode(),
    )
    fake_restic.respond(
        ("snapshots", "--no-lock", "--json", "--tag", "kind:files"),
        stdout=json.dumps(
            [snapshot("f0000001", T0 + timedelta(minutes=5), run_id, "files")]
        ).encode(),
    )
    fake_restic.respond(
        ("dump", "--no-lock", "--", "d0000001", restic.DB_FILENAME), stdout=b"PGDMP fake archive"
    )
    return run_id


class Started:
    """Every process the restore starts: its argv and its environment."""

    def __init__(self) -> None:
        self.argv: list[list[str]] = []
        self.env: list[Mapping[str, str]] = []


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> Started:
    record = Started()
    real_popen = subprocess.Popen
    real_run = subprocess.run

    def popen(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        record.argv.append(list(argv))
        record.env.append(dict(kwargs.get("env") or {}))  # type: ignore[call-overload]
        return real_popen(argv, **kwargs)  # type: ignore[call-overload,no-any-return]

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        record.argv.append(list(argv))
        record.env.append(dict(kwargs.get("env") or {}))  # type: ignore[call-overload]
        return real_run(argv, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr("apps.backup.restore.subprocess.Popen", popen)
    monkeypatch.setattr("apps.backup.restic.subprocess.run", run)
    return record


@pytest.fixture
def superuser(settings: Settings) -> None:
    settings.DATABASE_SUPERUSER = {
        "NAME": "brievie",
        "USER": "postgres",
        "PASSWORD": SUPERUSER_PASSWORD,
        "HOST": "postgres",
        "PORT": 5432,
    }


@pytest.fixture
def no_database(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Lock and closing replaced: the fake superuser reaches no database. Records the calls."""
    calls: list[str] = []

    @contextmanager
    def lock(conninfo: str) -> Iterator[None]:
        calls.append("lock")
        yield
        calls.append("unlock")

    def close(conninfo: str) -> int:
        calls.append("close")
        return 2

    monkeypatch.setattr("apps.backup.restore.database_lock", lock)
    monkeypatch.setattr("apps.backup.restore.close_interrupted_runs", close)
    return calls


@pytest.fixture
def pg_restore(fake_tool: Callable[[str], FakeBinary]) -> FakeBinary:
    return fake_tool("pg_restore")


# --- restore_database ------------------------------------------------------------------


def test_dump_is_piped_into_pg_restore_as_the_superuser(
    configured_folder: Path,
    fake_restic: FakeBinary,
    pg_restore: FakeBinary,
    superuser: None,
    no_database: list[str],
    started: Started,
) -> None:
    one_run(fake_restic)

    closed = restore_database(run=None, dbname="restore_probe")

    assert closed == 2
    assert pg_restore.calls == [[*PG_RESTORE_COMMAND[1:], "--dbname=restore_probe"]]
    assert fake_restic.calls[-1] == restic.dump_argv("d0000001", restic.DB_FILENAME, no_lock=True)
    (env,) = [
        env for argv, env in zip(started.argv, started.env, strict=True) if argv[0] == "pg_restore"
    ]
    assert env["PGUSER"] == "postgres"
    assert env["PGPASSWORD"] == SUPERUSER_PASSWORD
    assert env["PGHOST"] == "postgres"
    assert "RESTIC_PASSWORD" not in env
    # The lock is held around the restore and the closing of the runs.
    assert no_database == ["lock", "close", "unlock"]


def test_pg_restore_keeps_owners_and_restores_in_one_transaction() -> None:
    assert PG_RESTORE_COMMAND == (
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--no-password",
    )
    for dropped in ("--no-owner", "--no-privileges", "--no-acl", "--clean"):
        assert dropped not in PG_RESTORE_COMMAND


@pytest.mark.parametrize("failing", ["dump", "pg_restore"])
def test_a_failing_dump_or_restore_stops(
    configured_folder: Path,
    fake_restic: FakeBinary,
    pg_restore: FakeBinary,
    superuser: None,
    no_database: list[str],
    failing: str,
) -> None:
    one_run(fake_restic)
    if failing == "dump":
        fake_restic.respond(
            ("dump", "--no-lock", "--", "d0000001"),
            stdout=b"PGDMP trunc",
            stderr="read error",
            code=1,
        )
    else:
        pg_restore.respond((), stderr="pg_restore: error: could not execute query", code=1)

    with pytest.raises(BackupError) as caught:
        restore_database(run=None, dbname="restore_probe")

    assert caught.value.code == "restore_failed"
    assert "close" not in no_database


def test_pg_restore_errors_are_logged_without_row_data_or_secrets(
    configured_folder: Path,
    fake_restic: FakeBinary,
    pg_restore: FakeBinary,
    superuser: None,
    no_database: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    one_run(fake_restic)
    stderr = (
        "pg_restore: error: could not execute query: ERROR:  duplicate key value\n"
        f"pg_restore: error: password {SUPERUSER_PASSWORD} for user postgres\n"
        "DETAIL:  Key (email)=(jan@example.org) already exists.\n"
        'CONTEXT:  COPY documents_document, line 1: "Brief van de belastingdienst"\n'
        f"Command was: SET password = '{SUPERUSER_PASSWORD}'\n"
    )
    pg_restore.respond((), stderr=stderr, code=1)

    with pytest.raises(BackupError):
        restore_database(run=None, dbname="restore_probe")

    assert "duplicate key value" in caplog.text
    assert "for user postgres" in caplog.text  # only the password is hidden
    for leaked in ("jan@example.org", "belastingdienst", SUPERUSER_PASSWORD):
        assert leaked not in caplog.text


def test_restore_database_without_a_superuser_is_refused(
    configured_folder: Path, fake_restic: FakeBinary, pg_restore: FakeBinary, settings: Settings
) -> None:
    settings.DATABASE_SUPERUSER = None
    one_run(fake_restic)
    with pytest.raises(BackupError) as caught:
        restore_database(run=None, dbname="restore_probe")
    assert caught.value.code == "restore_failed"
    assert pg_restore.calls == []


@pytest.mark.django_db(transaction=True)
def test_restore_database_waits_for_no_back_up_that_holds_the_lock(
    configured_folder: Path,
    fake_restic: FakeBinary,
    pg_restore: FakeBinary,
    settings: Settings,
) -> None:
    # The owner stands in for the superuser: the lock is real, on the test database.
    owner = django_settings.DATABASE_OWNER
    assert owner is not None
    settings.DATABASE_SUPERUSER = dict(owner)
    one_run(fake_restic)
    with backup_lock(), pytest.raises(BackupError) as caught:
        restore_database(run=None, dbname=str(connection.settings_dict["NAME"]))
    assert caught.value.code == "busy"
    assert pg_restore.calls == []


@pytest.mark.django_db(transaction=True)
def test_restore_database_closes_the_runs_of_the_restored_database(
    configured_folder: Path,
    fake_restic: FakeBinary,
    pg_restore: FakeBinary,
    settings: Settings,
) -> None:
    owner = django_settings.DATABASE_OWNER
    assert owner is not None
    settings.DATABASE_SUPERUSER = dict(owner)
    one_run(fake_restic)
    BackupRun.objects.create(kind="backup", status=BackupRun.Status.RUNNING)

    assert restore_database(run=None, dbname=str(connection.settings_dict["NAME"])) == 1
    assert not BackupRun.objects.filter(status__in=["queued", "running"]).exists()


# --- database_lock ---------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_database_lock_and_backup_lock_exclude_each_other(owner_conninfo: str) -> None:
    with database_lock(owner_conninfo), pytest.raises(BackupError) as caught, backup_lock():
        pass
    assert caught.value.code == "busy"
    with backup_lock(), pytest.raises(BackupError) as caught, database_lock(owner_conninfo):
        pass
    assert caught.value.code == "busy"
    # Released afterwards: each can be taken again.
    with database_lock(owner_conninfo):
        pass
    with backup_lock():
        pass


@pytest.mark.django_db(transaction=True)
def test_database_lock_is_released_when_the_body_raises(owner_conninfo: str) -> None:
    with pytest.raises(ValueError, match="body"), database_lock(owner_conninfo):
        raise ValueError("body")
    with backup_lock():
        pass


def test_database_lock_hides_the_connection_string() -> None:
    conninfo = f"host=127.0.0.1 port=1 dbname=x user=postgres password={SUPERUSER_PASSWORD}"
    with pytest.raises(BackupError) as caught, database_lock(conninfo):
        pass
    assert caught.value.code == "restore_failed"
    assert SUPERUSER_PASSWORD not in str(caught.value)
    assert caught.value.__cause__ is None


# --- close_interrupted_runs ------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_open_runs_are_closed_by_a_restore(owner_conninfo: str) -> None:
    queued = BackupRun.objects.create(kind="backup", status=BackupRun.Status.QUEUED)
    running = BackupRun.objects.create(kind="check", status=BackupRun.Status.RUNNING)
    done = BackupRun.objects.create(kind="backup", status=BackupRun.Status.SUCCEEDED)

    assert close_interrupted_runs(owner_conninfo) == 2

    for run in (queued, running):
        run.refresh_from_db()
        assert run.status == BackupRun.Status.INTERRUPTED
        assert run.reason_code == "interrupted_by_restore"
        assert run.reason == "Afgebroken door herstel."
        assert run.finished_at is not None
    done.refresh_from_db()
    assert done.status == BackupRun.Status.SUCCEEDED
    assert done.reason_code == ""


@pytest.mark.django_db(transaction=True)
def test_a_closed_run_is_neither_resumed_nor_taken_for_abandoned(
    owner_conninfo: str, configured_folder: Path, fake_restic: FakeBinary
) -> None:
    run = BackupRun.objects.create(kind="backup", status=BackupRun.Status.RUNNING)
    close_interrupted_runs(owner_conninfo)

    services.perform_run(run.id)  # the restored job meets a closed run: no-op
    new = services.request_run("backup")

    assert fake_restic.calls == []
    assert new.id != run.id
    run.refresh_from_db()
    assert run.reason_code == "interrupted_by_restore"


# --- restore_files ---------------------------------------------------------------------


@pytest.fixture
def caddy(tmp_path: Path) -> Path:
    """Caddy's data volume as the admin container mounts it; returns the authority target.

    Caddy has started once, so its own `caddy` folder exists (the restore makes nothing
    above `…/caddy/pki`)."""
    (tmp_path / "caddy-data" / "caddy").mkdir(parents=True)
    return tmp_path / "caddy-data" / "caddy" / "pki" / "authorities" / "local"


def leaf_dir(authority_target: Path) -> Path:
    return authority_target.parents[2] / "certificates" / "local"


def restored_authority(files: tuple[str, ...]) -> Path:
    """What restic would have put back in the authority copy (the fake writes nothing)."""
    copy = Path(django_settings.BRIEVIE_AUTHORITY_COPY_DIR)
    copy.mkdir(parents=True, exist_ok=True)
    for name in files:
        (copy / name).write_text(f"restored {name}")
    # A temporary file of caddy-publish-root.sh that the back-up caught: never copied.
    (copy / "root.key.tmp").write_text("half a copy")
    return copy


class Chowned:
    """Records every fchown (the restore hands files over through descriptors)."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int]] = []

    def __eq__(self, other: object) -> bool:
        return self.calls == other

    def owners(self, *paths: Path) -> list[tuple[int, int] | None]:
        """For each path: the (uid, gid) it was handed to, or None."""
        found = {inode: (uid, gid) for inode, uid, gid in self.calls}
        return [found.get(path.stat().st_ino) for path in paths]


@pytest.fixture
def chowned(monkeypatch: pytest.MonkeyPatch) -> Chowned:
    recorded = Chowned()
    monkeypatch.setattr(
        "apps.backup.restore.os.fchown",
        lambda fd, uid, gid: recorded.calls.append((os.fstat(fd).st_ino, uid, gid)),
    )
    return recorded


@pytest.mark.parametrize("files", [AUTHORITY_FILES, ("root.crt", "root.key")])
@pytest.mark.parametrize("target_exists", [True, False])
@pytest.mark.django_db(transaction=True)
def test_files_are_restored_and_the_authority_placed(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    chowned: Chowned,
    files: tuple[str, ...],
    target_exists: bool,
) -> None:
    one_run(fake_restic)
    restored_authority(files)
    if target_exists:
        # A Caddy that started before the restore made an authority of its own.
        caddy.mkdir(parents=True)
        for name in AUTHORITY_FILES:
            (caddy / name).write_text(f"new {name}")
    leaf = leaf_dir(caddy)
    leaf.mkdir(parents=True)
    (leaf / "brievie.local.crt").write_text("leaf of the new authority")

    restored = restore_files(run=None, authority_target=caddy, clear_documents=False)

    assert restored.authority_placed is True
    assert fake_restic.calls[-1] == restic.restore_argv(
        "f0000001", "/", services.file_paths(), no_lock=True
    )
    assert sorted(p.name for p in caddy.iterdir()) == sorted(files)
    for name in files:
        assert (caddy / name).read_text() == f"restored {name}"
        assert stat.S_IMODE((caddy / name).stat().st_mode) == 0o600
    assert chowned.owners(caddy, *(caddy / name for name in files)) == [(0, 0)] * (len(files) + 1)
    assert stat.S_IMODE(caddy.stat().st_mode) == 0o700
    assert not leaf.exists()


@pytest.mark.django_db(transaction=True)
def test_a_backup_without_an_authority_leaves_caddy_alone(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    chowned: Chowned,
) -> None:
    # A public certificate (no internal authority): nothing to put back.
    one_run(fake_restic)
    caddy.mkdir(parents=True)
    (caddy / "root.crt").write_text("current")
    leaf = leaf_dir(caddy)
    leaf.mkdir(parents=True)

    placed = restore_files(run=None, authority_target=caddy, clear_documents=False)

    assert placed.authority_placed is False
    assert (caddy / "root.crt").read_text() == "current"
    assert leaf.exists()
    assert chowned == []


@pytest.mark.django_db(transaction=True)
def test_replace_never_places_the_authority_of_the_replaced_installation(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    chowned: Chowned,
) -> None:
    # The back-up has no authority; the copy and the config folder still hold the old ones.
    one_run(fake_restic)
    restored_authority(AUTHORITY_FILES)
    config = Path(django_settings.BRIEVIE_BACKUP_CONFIG_DIR)
    config.mkdir(parents=True)
    (config / "manifest.json").write_text("{}")
    (config / "env").write_text("OLD=1")
    caddy.mkdir(parents=True)
    (caddy / "root.crt").write_text("current")

    placed = restore_files(run=None, authority_target=caddy, clear_documents=True)

    assert placed.authority_placed is False
    assert list(Path(django_settings.BRIEVIE_AUTHORITY_COPY_DIR).iterdir()) == []
    assert list(config.iterdir()) == []
    assert Path(django_settings.BRIEVIE_AUTHORITY_COPY_DIR).is_dir()  # the mount point stays
    assert (caddy / "root.crt").read_text() == "current"
    assert chowned == []


@pytest.mark.django_db(transaction=True)
def test_replace_empties_the_documents_first(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    chowned: list[object],
    tmp_path: Path,
    settings: Settings,
) -> None:
    one_run(fake_restic)
    documents = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    # restic "restores" a file: it must survive, so the emptying came before.
    writer = tmp_path / "restic-that-writes"
    writer.write_text(
        "#!/bin/sh\n"
        f"if [ \"$1\" = restore ]; then mkdir -p '{documents}' && "
        f"echo restored > '{documents}/restored.page'; fi\n"
        f"exec '{fake_restic.path}' \"$@\"\n"
    )
    writer.chmod(0o755)
    settings.BRIEVIE_RESTIC_BINARY = str(writer)
    old = documents / str(uuid4()) / str(uuid4())
    old.mkdir(parents=True)
    (old / "1.page").write_bytes(b"old page")
    (documents / ".tmp-abc").write_bytes(b"half")
    outside = documents.parent / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    (documents / "link").symlink_to(outside)

    restore_files(run=None, authority_target=caddy, clear_documents=True)

    assert documents.is_dir()
    assert [p.name for p in documents.iterdir()] == ["restored.page"]
    assert (outside / "keep").exists()  # a link is removed, never followed


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("clear", [True, False])
@pytest.mark.parametrize(
    ("root", "expected"),
    [
        ("relative/documents", {"unsafe_path"}),
        ("", {"unsafe_path"}),
        ("/", {"unsafe_path"}),
        ("/data", {"unsafe_path"}),
        ("/usr/local", {"directory_not_allowed"}),
        ("/data/docu*", {"unsafe_path"}),
        ("/home/tester", {"unsafe_path"}),
        ("/Users/Tester/documents", {"unsafe_path"}),
        ("/etc/brievie", {"directory_not_allowed"}),
        ("link", {"unsafe_path"}),
        ("through-link", {"unsafe_path"}),
    ],
)
def test_restore_refuses_a_documents_root_it_cannot_trust(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    tmp_path: Path,
    settings: Settings,
    root: str,
    expected: set[str],
    clear: bool,
    recorder: Recorder,
) -> None:
    one_run(fake_restic)
    if root in ("link", "through-link"):
        real = tmp_path / "real-documents"
        (real / "documents").mkdir(parents=True)
        (real / "keep.page").write_text("keep")
        (real / "documents" / "keep.page").write_text("keep")
        (tmp_path / "link").symlink_to(real, target_is_directory=True)
        configured = tmp_path / "link" / ("documents" if root == "through-link" else "")
        settings.BRIEVIE_STORAGE_ROOT = str(configured)
    else:
        settings.BRIEVIE_STORAGE_ROOT = root

    # Never reach the real `/` or a home directory: every destructive primitive, and
    # every process start (restic included), fails the test. The refusal comes first.
    recorder.arm()
    with pytest.raises(BackupError) as caught:
        restore_files(run=None, authority_target=caddy, clear_documents=clear)
    recorder.disarm()

    assert caught.value.code in expected
    assert recorder.called == []
    assert not any(call[:1] == ["restore"] for call in fake_restic.calls)
    if root in ("link", "through-link"):
        assert (tmp_path / "real-documents" / "keep.page").exists()
        assert (tmp_path / "real-documents" / "documents" / "keep.page").exists()


@pytest.mark.django_db(transaction=True)
def test_a_failing_restore_after_emptying_says_the_documents_are_incomplete(
    configured_folder: Path, fake_restic: FakeBinary, caddy: Path
) -> None:
    one_run(fake_restic)
    fake_restic.respond(("restore",), stderr="Fatal: disk full", code=1)
    with pytest.raises(BackupError) as caught:
        restore_files(run=None, authority_target=caddy, clear_documents=True)
    assert caught.value.code == "documents_incomplete"
    assert "opnieuw" in caught.value.message
    assert "niet compleet" in caught.value.message


@pytest.mark.django_db(transaction=True)
def test_without_replace_the_documents_are_kept(
    configured_folder: Path, fake_restic: FakeBinary, caddy: Path, chowned: list[object]
) -> None:
    one_run(fake_restic)
    documents = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    documents.mkdir(parents=True)
    (documents / "kept").write_text("x")
    restore_files(run=None, authority_target=caddy, clear_documents=False)
    assert (documents / "kept").exists()


@pytest.mark.parametrize(
    ("code", "stderr", "expected"),
    [
        (3, "some files could not be restored", "restore_failed"),
        (1, "ignoring error for /data/documents/x", "restore_failed"),
        (12, "wrong password", "wrong_password"),
    ],
)
@pytest.mark.django_db(transaction=True)
def test_a_failing_files_restore_never_says_a_backup_was_made(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    chowned: list[object],
    code: int,
    stderr: str,
    expected: str,
) -> None:
    one_run(fake_restic)
    restored_authority(AUTHORITY_FILES)
    fake_restic.respond(("restore",), stderr=stderr, code=code)
    with pytest.raises(BackupError) as caught:
        restore_files(run=None, authority_target=caddy, clear_documents=False)
    assert caught.value.code == expected
    assert not caddy.exists()  # the authority is placed only after a whole restore


@pytest.mark.django_db(transaction=True)
def test_restore_files_waits_for_no_back_up_that_holds_the_lock(
    configured_folder: Path, fake_restic: FakeBinary, caddy: Path, owner_conninfo: str
) -> None:
    one_run(fake_restic)
    with psycopg.connect(owner_conninfo, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", [BACKUP_LOCK_KEY])
        with pytest.raises(BackupError) as caught:
            restore_files(run=None, authority_target=caddy, clear_documents=True)
    assert caught.value.code == "busy"
    assert not any(call[:1] == ["restore"] for call in fake_restic.calls)


# --- no secret in any argv -------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_no_secret_is_in_any_argv_of_a_restore(
    configured_folder: Path,
    fake_restic: FakeBinary,
    pg_restore: FakeBinary,
    superuser: None,
    no_database: list[str],
    caddy: Path,
    chowned: list[object],
    started: Started,
    settings: Settings,
) -> None:
    master = settings.BRIEVIE_MASTER_KEY
    one_run(fake_restic)
    restored_authority(AUTHORITY_FILES)

    restore_database(run=None, dbname="brievie")
    restore_files(run=None, authority_target=caddy, clear_documents=True)

    argvs = [*started.argv, *fake_restic.calls, *pg_restore.calls]
    assert any(argv[0] == "pg_restore" for argv in started.argv)
    assert any(call[:1] == ["restore"] for call in fake_restic.calls)
    for argv in argvs:
        joined = " ".join(argv)
        for secret in (SUPERUSER_PASSWORD, BACKUP_PASSWORD, master):
            assert secret not in joined
    for argv, env in zip(started.argv, started.env, strict=True):
        assert "BRIEVIE_MASTER_KEY" not in env
        if argv[0] == "pg_restore":
            assert env.get("PGPASSWORD") == SUPERUSER_PASSWORD
        else:
            assert env.get("RESTIC_PASSWORD") == BACKUP_PASSWORD
            assert "PGPASSWORD" not in env


# --- the commands ----------------------------------------------------------------------


def test_restore_database_command_prints_the_closed_runs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, object] = {}

    def fake(*, run: str | None, dbname: str) -> int:
        seen.update(run=run, dbname=dbname)
        return 3

    monkeypatch.setattr("apps.backup.restore.restore_database", fake)
    call_command("restore_database", "--run", "r1", "--dbname", "restore_probe")
    assert seen == {"run": "r1", "dbname": "restore_probe"}
    assert "interrupted_runs=3" in capsys.readouterr().out
    call_command("restore_database")
    assert seen == {"run": None, "dbname": "brievie"}


def test_restore_database_command_says_why_it_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_kwargs: object) -> int:
        raise BackupError("wrong_password")

    monkeypatch.setattr("apps.backup.restore.restore_database", fail)
    with pytest.raises(CommandError) as caught:
        call_command("restore_database")
    assert str(caught.value) == "Het wachtwoord van de back-up klopt niet."
    assert caught.value.returncode == 1


def test_restore_files_command_places_the_authority_for_caddy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict[str, object]] = []

    def fake(**kwargs: object) -> FilesRestored:
        seen.append(kwargs)
        return FilesRestored(authority_placed=True, removed=0)

    monkeypatch.setattr("apps.backup.restore.restore_files", fake)
    call_command("restore_files", "--run", "r1", "--replace")
    call_command("restore_files")
    target = Path("/caddy-data/caddy/pki/authorities/local")
    assert seen == [
        {"run": "r1", "authority_target": target, "clear_documents": True},
        {"run": None, "authority_target": target, "clear_documents": False},
    ]


@pytest.mark.parametrize("placed", [True, False])
def test_restore_files_command_says_when_caddy_keeps_its_own_authority(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], placed: bool
) -> None:
    restored = FilesRestored(authority_placed=placed, removed=3)
    monkeypatch.setattr("apps.backup.restore.restore_files", lambda **_kwargs: restored)
    call_command("restore_files")
    out = capsys.readouterr().out
    sentence = "De back-up heeft geen eigen certificaat; Caddy houdt zijn huidige certificaat."
    assert (sentence in out) is not placed
    assert (
        "Opgeruimd na het terugzetten: 3 onderdelen die geen gewoon bestand of map waren "
        "(zoals snelkoppelingen)." in out
    )


def test_restore_files_command_says_why_it_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_kwargs: object) -> None:
        raise BackupError("busy")

    monkeypatch.setattr("apps.backup.restore.restore_files", fail)
    with pytest.raises(CommandError) as caught:
        call_command("restore_files")
    assert str(caught.value) == "Er is al een back-up of herstel bezig."


@pytest.mark.django_db(transaction=True)
def test_documents_root_mode_is_kept(
    configured_folder: Path, fake_restic: FakeBinary, caddy: Path, chowned: list[object]
) -> None:
    # Emptying keeps the mount point itself (a volume), with its owner and mode.
    one_run(fake_restic)
    documents = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    documents.mkdir(parents=True, mode=0o700)
    (documents / "old").write_text("x")
    inode = documents.stat().st_ino
    restore_files(run=None, authority_target=caddy, clear_documents=True)
    assert documents.stat().st_ino == inode
    assert stat.S_IMODE(documents.stat().st_mode) == 0o700


def test_restore_files_command_says_the_step_can_run_again(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(**_kwargs: object) -> bool:
        raise BackupError("documents_incomplete")

    monkeypatch.setattr("apps.backup.restore.restore_files", fail)
    with pytest.raises(CommandError) as caught:
        call_command("restore_files", "--replace")
    assert str(caught.value) == (
        "Het terugzetten van de documenten is afgebroken, dus de documenten zijn nu niet "
        "compleet. Voer deze stap gewoon opnieuw uit."
    )


# --- after restic: only regular files and directories stay -------------------------------


def restic_that_restores(tmp_path: Path, fake: FakeBinary, script: str) -> str:
    """A restic that runs `script` (sh) on `restore`, then answers as the fake does."""
    writer = tmp_path / "restic-that-writes"
    writer.write_text(
        f'#!/bin/sh\nif [ "$1" = restore ]; then\n{script}\nfi\nexec \'{fake.path}\' "$@"\n'
    )
    writer.chmod(0o755)
    return str(writer)


@pytest.mark.django_db(transaction=True)
def test_a_restore_removes_links_and_special_files_and_clears_setuid(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    tmp_path: Path,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    one_run(fake_restic)
    documents = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    # What a hostile snapshot could hold inside the documents.
    settings.BRIEVIE_RESTIC_BINARY = restic_that_restores(
        tmp_path,
        fake_restic,
        f"mkdir -p '{documents}/ws'\n"
        f"echo page > '{documents}/ws/1.page'\n"
        f"chmod 4755 '{documents}/ws/1.page'\n"
        f"ln -s '{outside}' '{documents}/ws/escape'\n"
        f"ln -s '{outside}/keep' '{documents}/keep-link'\n"
        f"mkfifo '{documents}/ws/fifo'",
    )

    restored = restore_files(run=None, authority_target=caddy, clear_documents=False)

    assert restored.removed == 3
    assert sorted(p.name for p in (documents / "ws").iterdir()) == ["1.page"]
    assert not (documents / "keep-link").exists()
    assert stat.S_IMODE((documents / "ws" / "1.page").stat().st_mode) == 0o755
    assert (outside / "keep").read_text() == "keep"


@pytest.mark.django_db(transaction=True)
def test_as_root_the_restored_trees_go_to_the_keeper(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    tmp_path: Path,
    settings: Settings,
    chowned: Chowned,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one_run(fake_restic)
    documents = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    config = Path(django_settings.BRIEVIE_BACKUP_CONFIG_DIR)
    settings.BRIEVIE_RESTIC_BINARY = restic_that_restores(
        tmp_path,
        fake_restic,
        f"mkdir -p '{documents}/ws' '{config}'\n"
        f"echo page > '{documents}/ws/1.page'\n"
        f"echo env > '{config}/env'",
    )
    monkeypatch.setattr("apps.backup.restore.os.geteuid", lambda: 0)

    restore_files(run=None, authority_target=caddy, clear_documents=False)

    paths = (documents, documents / "ws", documents / "ws" / "1.page", config, config / "env")
    assert chowned.owners(*paths) == [(10001, 10001)] * len(paths)


@pytest.mark.django_db(transaction=True)
def test_restic_restores_exactly_the_three_judged_directories(
    configured_folder: Path, fake_restic: FakeBinary, caddy: Path
) -> None:
    one_run(fake_restic)
    restore_files(run=None, authority_target=caddy, clear_documents=False)
    call = fake_restic.calls[-1]
    assert call[:4] == ["restore", "--no-lock", "--target", "/"]
    assert call[-2:] == ["--", "f0000001"]
    includes = [call[i + 1] for i, arg in enumerate(call) if arg == "--include"]
    assert includes == [
        django_settings.BRIEVIE_STORAGE_ROOT,
        django_settings.BRIEVIE_AUTHORITY_COPY_DIR,
        django_settings.BRIEVIE_BACKUP_CONFIG_DIR,
    ]


@pytest.mark.django_db(transaction=True)
def test_every_restic_call_of_a_restore_reads_without_a_lock(
    configured_folder: Path,
    fake_restic: FakeBinary,
    pg_restore: FakeBinary,
    superuser: None,
    no_database: list[str],
    caddy: Path,
    chowned: Chowned,
) -> None:
    # The source may be mounted read-only: restic must not try to write a lock there.
    one_run(fake_restic)
    restore_database(run=None, dbname="brievie")
    restore_files(run=None, authority_target=caddy, clear_documents=False)
    assert fake_restic.calls
    assert all(call[1] == "--no-lock" for call in fake_restic.calls), fake_restic.calls


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("planted", ["link", "dir-link", "hard-link"])
def test_a_restore_without_replace_refuses_a_link_already_in_a_target(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    tmp_path: Path,
    recorder: Recorder,
    planted: str,
) -> None:
    one_run(fake_restic)
    documents = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    (documents / "ws").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    if planted == "link":
        (documents / "ws" / "1.page").symlink_to(outside / "keep")
    elif planted == "dir-link":
        (documents / "ws" / "doc").symlink_to(outside, target_is_directory=True)
    else:
        os.link(outside / "keep", documents / "ws" / "1.page")

    # Listing and the fake restic (find_run) are allowed; nothing destructive is.
    recorder.arm(allow=("scandir", "Popen", "run"))
    with pytest.raises(BackupError) as caught:
        restore_files(run=None, authority_target=caddy, clear_documents=False)
    recorder.disarm()

    assert caught.value.code == "restore_target_unsafe"
    assert "--replace" in caught.value.message
    assert not any(call[:1] == ["restore"] for call in fake_restic.calls)
    assert (outside / "keep").read_text() == "keep"


@pytest.mark.django_db(transaction=True)
def test_a_restore_removes_a_restored_file_with_another_name_elsewhere(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    tmp_path: Path,
    settings: Settings,
) -> None:
    one_run(fake_restic)
    documents = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    settings.BRIEVIE_RESTIC_BINARY = restic_that_restores(
        tmp_path,
        fake_restic,
        f"mkdir -p '{documents}/ws'\nln '{outside}/keep' '{documents}/ws/1.page'",
    )

    restored = restore_files(run=None, authority_target=caddy, clear_documents=False)

    assert restored.removed == 1
    assert not (documents / "ws" / "1.page").exists()
    assert (outside / "keep").read_text() == "keep"


@pytest.mark.django_db(transaction=True)
def test_on_a_fresh_machine_the_authority_is_placed_before_caddy_first_starts(
    configured_folder: Path, fake_restic: FakeBinary, tmp_path: Path, chowned: Chowned
) -> None:
    # Only the volume (the stand-in for /caddy-data) exists: Caddy never ran here.
    one_run(fake_restic)
    restored_authority(AUTHORITY_FILES)
    caddy_data = tmp_path / "fresh-caddy-data"
    caddy_data.mkdir()
    target = caddy_data / "caddy" / "pki" / "authorities" / "local"

    restored = restore_files(run=None, authority_target=target, clear_documents=False)

    assert restored.authority_placed is True
    for directory in (target, *target.parents[:3]):
        assert directory.is_dir()
        assert not directory.is_symlink()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700, directory
    assert sorted(p.name for p in target.iterdir()) == sorted(AUTHORITY_FILES)
    for name in AUTHORITY_FILES:
        assert stat.S_IMODE((target / name).stat().st_mode) == 0o600
    # The leaf directory is only removed when there is one; it is never made.
    assert sorted(p.name for p in (caddy_data / "caddy").iterdir()) == ["pki"]


@pytest.mark.django_db(transaction=True)
def test_the_authority_is_never_placed_when_caddys_volume_is_missing(
    configured_folder: Path, fake_restic: FakeBinary, tmp_path: Path, chowned: Chowned
) -> None:
    one_run(fake_restic)
    restored_authority(AUTHORITY_FILES)
    target = tmp_path / "unmounted-caddy-data" / "caddy" / "pki" / "authorities" / "local"

    with pytest.raises(BackupError) as caught:
        restore_files(run=None, authority_target=target, clear_documents=False)

    assert caught.value.code == "caddy_not_ready"
    assert not (tmp_path / "unmounted-caddy-data").exists()


# --- nothing changes before the run is found ------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_restore_database_of_a_missing_run_touches_nothing(
    configured_folder: Path,
    fake_restic: FakeBinary,
    pg_restore: FakeBinary,
    superuser: None,
    no_database: list[str],
) -> None:
    one_run(fake_restic)
    missing = str(uuid4())

    with pytest.raises(BackupError) as caught:
        restore_database(run=missing, dbname="brievie")

    assert caught.value.code == "run_not_found"
    assert pg_restore.calls == []  # pg_restore never started
    assert no_database == []  # neither locked nor any run closed
    assert not any(call[:1] == ["dump"] for call in fake_restic.calls)


@pytest.mark.django_db(transaction=True)
def test_restore_files_of_a_missing_run_touches_nothing(
    configured_folder: Path, fake_restic: FakeBinary, caddy: Path, recorder: Recorder
) -> None:
    one_run(fake_restic)
    documents = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    documents.mkdir(parents=True)
    (documents / "kept.page").write_text("kept")

    recorder.arm(allow=("Popen", "run"))  # the fake restic lists the runs; nothing else
    with pytest.raises(BackupError) as caught:
        restore_files(run=str(uuid4()), authority_target=caddy, clear_documents=True)
    recorder.disarm()

    assert caught.value.code == "run_not_found"
    assert (documents / "kept.page").read_text() == "kept"
    assert not any(call[:1] == ["restore"] for call in fake_restic.calls)


# --- restore_check: does the run still have both snapshots? ---------------------------------


@pytest.mark.django_db(transaction=True)
def test_restore_check_says_a_complete_run_can_be_restored(
    configured_folder: Path, fake_restic: FakeBinary, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = one_run(fake_restic)

    call_command("restore_check", "--run", str(run_id))

    assert capsys.readouterr().out.strip() == (
        f"De back-up {run_id} is compleet: de database en de bestanden zijn er allebei."
    )
    assert fake_restic.calls
    assert all(call[:2] in (["snapshots", "--no-lock"],) for call in fake_restic.calls)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("which", ["missing", "only-db", "malformed"])
def test_restore_check_refuses_a_run_that_cannot_be_restored(
    configured_folder: Path, fake_restic: FakeBinary, which: str
) -> None:
    run_id = one_run(fake_restic)
    if which == "only-db":
        fake_restic.respond(
            ("snapshots", "--no-lock", "--json", "--tag", "kind:files"), stdout=b"[]"
        )
    run = {"missing": str(uuid4()), "only-db": str(run_id), "malformed": "not-a-run"}[which]

    with pytest.raises(CommandError) as caught:
        call_command("restore_check", "--run", run)

    assert caught.value.returncode == 1
    assert str(caught.value).startswith("Deze back-up is niet gevonden.")
    assert "./brievie status --list" in str(caught.value)

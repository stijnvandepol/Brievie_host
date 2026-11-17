"""Restore, part 2: the database, the files, and closing the runs the dump brought (Task 10)."""

import json
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
    close_interrupted_runs,
    restore_database,
    restore_files,
)
from tests.backup_support import FakeBinary

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
        ("snapshots", "--json", "--tag", "kind:db"),
        stdout=json.dumps([snapshot("db1", T0, run_id, "db")]).encode(),
    )
    fake_restic.respond(
        ("snapshots", "--json", "--tag", "kind:files"),
        stdout=json.dumps(
            [snapshot("files1", T0 + timedelta(minutes=5), run_id, "files")]
        ).encode(),
    )
    fake_restic.respond(("dump", "db1", restic.DB_FILENAME), stdout=b"PGDMP fake archive")
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
    assert fake_restic.calls[-1] == restic.dump_argv("db1", restic.DB_FILENAME)
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
        fake_restic.respond(("dump", "db1"), stdout=b"PGDMP trunc", stderr="read error", code=1)
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
    """Caddy's data volume as the admin container mounts it; returns the authority target."""
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


@pytest.fixture
def chowned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int]]:
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(
        "apps.backup.restore.os.chown",
        lambda path, uid, gid: calls.append((Path(path).name, uid, gid)),
    )
    return calls


@pytest.mark.parametrize("files", [AUTHORITY_FILES, ("root.crt", "root.key")])
@pytest.mark.parametrize("target_exists", [True, False])
@pytest.mark.django_db(transaction=True)
def test_files_are_restored_and_the_authority_placed(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    chowned: list[tuple[str, int, int]],
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

    restore_files(run=None, authority_target=caddy, clear_documents=False)

    assert fake_restic.calls[-1] == restic.restore_argv("files1", "/", services.file_paths())
    assert sorted(p.name for p in caddy.iterdir()) == sorted(files)
    for name in files:
        assert (caddy / name).read_text() == f"restored {name}"
        assert stat.S_IMODE((caddy / name).stat().st_mode) == 0o600
        assert (name, 0, 0) in chowned
    assert stat.S_IMODE(caddy.stat().st_mode) == 0o700
    assert (caddy.name, 0, 0) in chowned
    assert not leaf.exists()


@pytest.mark.django_db(transaction=True)
def test_a_backup_without_an_authority_leaves_caddy_alone(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    chowned: list[tuple[str, int, int]],
) -> None:
    # A public certificate (no internal authority): nothing to put back.
    one_run(fake_restic)
    caddy.mkdir(parents=True)
    (caddy / "root.crt").write_text("current")
    leaf = leaf_dir(caddy)
    leaf.mkdir(parents=True)

    placed = restore_files(run=None, authority_target=caddy, clear_documents=False)

    assert placed is False
    assert (caddy / "root.crt").read_text() == "current"
    assert leaf.exists()
    assert chowned == []


@pytest.mark.django_db(transaction=True)
def test_replace_never_places_the_authority_of_the_replaced_installation(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    chowned: list[tuple[str, int, int]],
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

    assert placed is False
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
@pytest.mark.parametrize("root", ["relative/documents", "/", "link"])
def test_replace_refuses_a_documents_root_it_cannot_trust(
    configured_folder: Path,
    fake_restic: FakeBinary,
    caddy: Path,
    tmp_path: Path,
    settings: Settings,
    root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never_list(path: object) -> None:
        # Never reach the real `/`: a refusal comes before anything is listed.
        raise AssertionError("the root was listed")

    monkeypatch.setattr("apps.backup.restore.os.scandir", never_list)
    one_run(fake_restic)
    if root == "link":
        real = tmp_path / "real-documents"
        real.mkdir()
        (real / "keep.page").write_text("keep")
        (tmp_path / "link").symlink_to(real)
        settings.BRIEVIE_STORAGE_ROOT = str(tmp_path / "link")
    else:
        settings.BRIEVIE_STORAGE_ROOT = root

    with pytest.raises(BackupError) as caught:
        restore_files(run=None, authority_target=caddy, clear_documents=True)

    assert caught.value.code == "restore_failed"
    assert not any(call[:1] == ["restore"] for call in fake_restic.calls)
    if root == "link":
        assert (tmp_path / "real-documents" / "keep.page").exists()


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

    def fake(**kwargs: object) -> bool:
        seen.append(kwargs)
        return True

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
    monkeypatch.setattr("apps.backup.restore.restore_files", lambda **_kwargs: placed)
    call_command("restore_files")
    out = capsys.readouterr().out
    sentence = "De back-up heeft geen eigen certificaat; Caddy houdt zijn huidige certificaat."
    assert (sentence in out) is not placed


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

"""Restore, part 1: open the back-up with the master key and write the restored .env (Task 9)."""

import json
import os
import re
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from pytest_django import Settings

from apps.backup import restic
from apps.backup.envcopy import EXCLUDED_KEYS, HOST_KEYS, restored_env
from apps.backup.errors import BackupError
from apps.backup.manifest import MANIFEST_NAME
from apps.backup.restore import (
    check_master_key_against_dump,
    fetch,
    find_run,
)
from apps.common import crypto
from tests.backup_support import FakeBinary

MASTER = "5a" * 32
OTHER_MASTER = "00" * 32
SEPARATE = "separate-backup-password-0123"
DERIVED = crypto.derive_backup_password(bytes.fromhex(MASTER))
WORKSPACE = UUID("6f1c2d3e-4b5a-4c6d-8e7f-0a1b2c3d4e5f")
PASSWORD_LINE = "BRIEVIE_BACKUP_PASSWORD="
T0 = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)

ENV_COPY = """\
# Brievie settings
POSTGRES_PORT=5433
DATABASE_URL=postgres://brievie_app:app-pw-1@localhost:5433/brievie
SECRET_KEY=keep-this-secret-key-of-fifty-characters-or-more-abcdef
BRIEVIE_HOSTNAME=brievie.example
BRIEVIE_APP_PASSWORD=app-pw-1
BRIEVIE_BACKUP_DB_PASSWORD=db-pw-1
DATABASE_BACKUP_URL=postgres://brievie_backup:db-pw-1@localhost:5433/brievie
# BRIEVIE_MASTER_KEY=
BRIEVIE_BACKUP_DIR=/mnt/old-disk
BRIEVIE_BACKUP_PASSWORD=old-password
DEBUG=false
"""


def snapshot(snapshot_id: str, when: datetime, run_id: UUID, kind: str) -> dict[str, object]:
    path = "/brievie.pgdump" if kind == "db" else "/data/documents"
    return {
        "id": snapshot_id,
        "time": when.isoformat(),
        "tags": [f"run:{run_id}", f"kind:{kind}"],
        "paths": [path],
    }


def manifest_json(run_id: UUID, *, migrations: dict[str, list[str]] | None = None) -> bytes:
    data = {
        "format": 1,
        "run_id": str(run_id),
        "created_at": "2026-11-10T03:00:00+00:00",
        "brievie_version": "0.9.0",
        "migrations": migrations if migrations is not None else {"backup": ["0001_initial"]},
        "postgres_version": "18.1",
        "hostname": "brievie.example",
        "authority_sha256": None,
        "workspaces": {},
    }
    return json.dumps(data).encode()


def copy_block(rows: list[tuple[UUID, bytes]]) -> bytes:
    lines = [
        "--",
        "-- PostgreSQL database dump",
        "--",
        "SET statement_timeout = 0;",
        "COPY public.workspaces_workspacekey "
        "(id, created_at, updated_at, workspace_id, wrapped_key) FROM stdin;",
    ]
    for workspace_id, wrapped in rows:
        lines.append(
            "\t".join(
                [
                    str(uuid4()),
                    "2026-11-01 10:00:00+00",
                    "2026-11-01 10:00:00+00",
                    str(workspace_id),
                    "\\\\x" + wrapped.hex(),
                ]
            )
        )
    lines += ["\\.", "", ""]
    return "\n".join(lines).encode()


def sealed_key(master_hex: str, workspace_id: UUID = WORKSPACE) -> bytes:
    return crypto.seal(bytes.fromhex(master_hex), crypto.new_key(), aad=workspace_id.bytes)


class Backup:
    """The fake repository of one run: restic answers, pg_restore prints the key table."""

    def __init__(self, restic_fake: FakeBinary, pg_restore: FakeBinary, config_dir: str) -> None:
        self.restic = restic_fake
        self.pg_restore = pg_restore
        self.config_dir = config_dir
        self.run_id = uuid4()

    def snapshots(self, db: list[dict[str, object]], files: list[dict[str, object]]) -> None:
        self.restic.respond(
            ("snapshots", "--json", "--tag", "kind:db"), stdout=json.dumps(db).encode()
        )
        self.restic.respond(
            ("snapshots", "--json", "--tag", "kind:files"), stdout=json.dumps(files).encode()
        )

    def contents(
        self,
        *,
        files_id: str = "f0000001",
        db_id: str = "d0000001",
        manifest: bytes | None = None,
        env: str = ENV_COPY,
        keys: bytes | None = None,
    ) -> None:
        self.restic.respond(
            ("dump", "--", files_id, f"{self.config_dir}/{MANIFEST_NAME}"),
            stdout=manifest if manifest is not None else manifest_json(self.run_id),
        )
        self.restic.respond(("dump", "--", files_id, f"{self.config_dir}/env"), stdout=env.encode())
        self.restic.respond(("dump", "--", db_id, restic.DB_FILENAME), stdout=b"PGDMP fake archive")
        self.pg_restore.respond(
            (), stdout=keys if keys is not None else copy_block([(WORKSPACE, sealed_key(MASTER))])
        )

    def one_run(self, **kwargs: object) -> None:
        self.snapshots(
            [snapshot("d0000001", T0, self.run_id, "db")],
            [snapshot("f0000001", T0 + timedelta(minutes=5), self.run_id, "files")],
        )
        self.contents(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def backup(
    configured_folder: Path,
    fake_restic: FakeBinary,
    fake_tool: Callable[[str], FakeBinary],
    settings: Settings,
) -> Backup:
    settings.BRIEVIE_BACKUP_PASSWORD = ""
    return Backup(fake_restic, fake_tool("pg_restore"), settings.BRIEVIE_BACKUP_CONFIG_DIR)


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "restore-out"
    directory.mkdir()
    return directory


def record_fchown(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int, int]]:
    """The restore hands its files over through descriptors: record (inode, uid, gid)."""
    calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        "apps.backup.restore.os.fchown",
        lambda fd, uid, gid: calls.append((os.fstat(fd).st_ino, uid, gid)),
    )
    return calls


def owned_names(calls: list[tuple[int, int, int]], directory: Path) -> list[tuple[str, int, int]]:
    """The names in (and of) `directory` that were handed over, with uid and gid."""
    names = {path.stat().st_ino: path.name for path in [directory, *directory.iterdir()]}
    return sorted((names[inode], uid, gid) for inode, uid, gid in calls if inode in names)


def env_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def do_fetch(out_dir: Path, *, master: str = MASTER, separate: str | None = None) -> object:
    return fetch(
        run=None,
        master_key_hex=master,
        separate_password=separate,
        backup_dir_host=None,
        out_dir=out_dir,
        out_owner=None,
    )


# --- fetch -----------------------------------------------------------------------------


def test_fetch_writes_env_and_manifest(backup: Backup, out_dir: Path) -> None:
    backup.one_run()
    found = do_fetch(out_dir)
    assert found.run_id == backup.run_id  # type: ignore[attr-defined]
    assert sorted(p.name for p in out_dir.iterdir()) == ["env", "manifest.json"]
    for name in ("env", "manifest.json"):
        assert stat.S_IMODE(os.stat(out_dir / name).st_mode) == 0o600
    assert json.loads((out_dir / "manifest.json").read_text())["run_id"] == str(backup.run_id)
    values = env_values((out_dir / "env").read_text())
    assert values["BRIEVIE_MASTER_KEY"] == MASTER
    copied = set(env_values(ENV_COPY).values())
    for key in ("POSTGRES_PASSWORD", "BRIEVIE_OWNER_PASSWORD"):
        assert re.fullmatch(r"[0-9a-f]{48}", values[key]), key
        assert values[key] not in copied, key
    assert values["SECRET_KEY"].startswith("keep-this-secret-key")
    # The keeper has no master key: the restored .env carries the password that opened it.
    assert values["BRIEVIE_BACKUP_PASSWORD"] == DERIVED


def test_fetch_with_a_separate_password_writes_that_password(backup: Backup, out_dir: Path) -> None:
    backup.one_run()
    do_fetch(out_dir, separate=SEPARATE)
    assert env_values((out_dir / "env").read_text())["BRIEVIE_BACKUP_PASSWORD"] == SEPARATE
    restic_env_keys = backup.restic.env_keys
    assert all("RESTIC_PASSWORD" in keys for keys in restic_env_keys)


def test_fetch_with_a_wrong_password_writes_nothing(backup: Backup, out_dir: Path) -> None:
    backup.restic.respond(("snapshots",), stderr="wrong password or no key found", code=12)
    with pytest.raises(BackupError) as caught:
        do_fetch(out_dir)
    assert caught.value.code == "wrong_password"
    assert list(out_dir.iterdir()) == []


def test_wrong_master_key_with_a_separate_password_is_caught_from_the_dump(
    backup: Backup, out_dir: Path
) -> None:
    backup.one_run(keys=copy_block([(WORKSPACE, sealed_key(MASTER))]))
    with pytest.raises(BackupError) as caught:
        do_fetch(out_dir, master=OTHER_MASTER, separate=SEPARATE)
    assert caught.value.code == "wrong_master_key"
    assert list(out_dir.iterdir()) == []


@pytest.mark.parametrize("master", ["abc", "5a" * 31, "5a" * 33, "zz" * 32, ""])
def test_a_malformed_master_key_is_refused_before_restic(
    backup: Backup, out_dir: Path, master: str
) -> None:
    backup.one_run()
    with pytest.raises(BackupError) as caught:
        do_fetch(out_dir, master=master)
    assert caught.value.code == "master_key_malformed"
    assert master not in str(caught.value) or master == ""
    assert list(out_dir.iterdir()) == []
    assert backup.restic.calls == []


def test_a_master_key_typed_with_spaces_dashes_and_capitals_is_accepted(
    backup: Backup, out_dir: Path
) -> None:
    backup.one_run()
    typed = "-".join(MASTER.upper()[i : i + 8] for i in range(0, 64, 8))
    typed = " " + typed[:20] + " \t" + typed[20:] + "\n"
    do_fetch(out_dir, master=typed)
    assert env_values((out_dir / "env").read_text())["BRIEVIE_MASTER_KEY"] == MASTER


def test_the_typed_master_key_is_redacted_from_restic_output(
    backup: Backup, out_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    backup.restic.respond(("snapshots",), stderr=f"echo {MASTER} and {MASTER.upper()}", code=1)
    with pytest.raises(BackupError):
        do_fetch(out_dir, master=MASTER.upper())
    assert caplog.records
    assert MASTER not in caplog.text
    assert MASTER.upper() not in caplog.text


def test_fetch_hands_a_folder_it_creates_to_the_owner(
    backup: Backup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup.one_run()
    calls = record_fchown(monkeypatch)
    created = tmp_path / "new-out"
    fetch(
        run=None,
        master_key_hex=MASTER,
        separate_password=None,
        backup_dir_host=None,
        out_dir=created,
        out_owner=(501, 20),
    )
    assert ("new-out", 501, 20) in owned_names(calls, created)
    assert stat.S_IMODE(os.stat(created).st_mode) == 0o700


def test_fetch_leaves_the_owner_of_an_existing_folder(
    backup: Backup, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup.one_run()
    calls = record_fchown(monkeypatch)
    fetch(
        run=None,
        master_key_hex=MASTER,
        separate_password=None,
        backup_dir_host=None,
        out_dir=out_dir,
        out_owner=(501, 20),
    )
    assert out_dir.name not in [name for name, _uid, _gid in owned_names(calls, out_dir)]


def test_fetch_refuses_a_backup_with_unknown_migrations(backup: Backup, out_dir: Path) -> None:
    newer = manifest_json(backup.run_id, migrations={"documents": ["9999_future"]})
    backup.one_run(manifest=newer)
    with pytest.raises(BackupError) as caught:
        do_fetch(out_dir)
    assert caught.value.code == "newer_backup"
    assert list(out_dir.iterdir()) == []
    assert backup.pg_restore.calls == []


def test_fetch_refuses_an_unreadable_manifest(backup: Backup, out_dir: Path) -> None:
    backup.one_run(manifest=b"{not json")
    with pytest.raises(BackupError) as caught:
        do_fetch(out_dir)
    assert caught.value.code == "manifest_unreadable"
    assert list(out_dir.iterdir()) == []


def test_fetch_refuses_a_manifest_of_another_run(backup: Backup, out_dir: Path) -> None:
    backup.one_run(manifest=manifest_json(uuid4()))
    with pytest.raises(BackupError) as caught:
        do_fetch(out_dir)
    assert caught.value.code == "manifest_unreadable"
    assert list(out_dir.iterdir()) == []


def test_fetch_of_an_unknown_run_writes_nothing(backup: Backup, out_dir: Path) -> None:
    backup.one_run()
    with pytest.raises(BackupError) as caught:
        fetch(
            run=str(uuid4()),
            master_key_hex=MASTER,
            separate_password=None,
            backup_dir_host=None,
            out_dir=out_dir,
            out_owner=None,
        )
    assert caught.value.code == "run_not_found"
    assert list(out_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("code", "stderr", "expected"),
    [
        (12, "wrong password", "wrong_password"),
        (10, "repository does not exist", "repository_missing"),
        (1, "dial tcp: connection refused", "destination_unreachable"),
        (3, "some files could not be read", "restore_failed"),
        (1, "path not found in snapshot", "restore_failed"),
    ],
)
def test_a_failing_dump_of_the_manifest_never_says_a_backup_was_made(
    backup: Backup, out_dir: Path, code: int, stderr: str, expected: str
) -> None:
    backup.one_run()
    backup.restic.respond(("dump", "--", "f0000001"), stderr=stderr, code=code)
    with pytest.raises(BackupError) as caught:
        do_fetch(out_dir)
    assert caught.value.code == expected
    assert list(out_dir.iterdir()) == []


def test_fetch_sets_the_owner_of_the_files(
    backup: Backup, out_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup.one_run()
    calls = record_fchown(monkeypatch)
    fetch(
        run=None,
        master_key_hex=MASTER,
        separate_password=None,
        backup_dir_host="/media/backup",
        out_dir=out_dir,
        out_owner=(501, 20),
    )
    assert owned_names(calls, out_dir) == [("env", 501, 20), ("manifest.json", 501, 20)]
    assert env_values((out_dir / "env").read_text())["BRIEVIE_BACKUP_DIR"] == "/media/backup"


def test_no_secret_is_in_any_argv_of_a_full_fetch(backup: Backup, out_dir: Path) -> None:
    backup.one_run()
    do_fetch(out_dir, separate=SEPARATE)
    calls = backup.restic.calls + backup.pg_restore.calls
    assert backup.pg_restore.calls, "the dump was read through pg_restore"
    assert any(call[:1] == ["dump"] for call in backup.restic.calls)
    for argv in calls:
        joined = " ".join(argv)
        for secret in (MASTER, SEPARATE, DERIVED, "p" * 40):
            assert secret not in joined
    # pg_restore needs no secret at all; restic gets its password from the environment.
    for keys in backup.pg_restore.env_keys:
        assert "RESTIC_PASSWORD" not in keys
        assert "BRIEVIE_MASTER_KEY" not in keys
    assert backup.pg_restore.calls == [
        ["--data-only", "--table=workspaces_workspacekey", "--file=-"]
    ]


# --- find_run --------------------------------------------------------------------------


def test_latest_run_needs_both_snapshots(backup: Backup, configured_folder: Path) -> None:
    complete, newest = uuid4(), uuid4()
    backup.snapshots(
        [
            snapshot("d0000022", T0 + timedelta(days=1), newest, "db"),
            snapshot("d0000021", T0, complete, "db"),
        ],
        [snapshot("f0000021", T0 + timedelta(minutes=5), complete, "files")],
    )
    found = find_run(restic_repository(), "pw", None)
    assert found.run_id == complete
    assert (found.db.id, found.files.id) == ("d0000021", "f0000021")


def test_a_resumed_run_takes_the_newest_snapshot_of_each_kind(
    backup: Backup, configured_folder: Path
) -> None:
    run_id = backup.run_id
    backup.snapshots(
        [
            snapshot("d0000011", T0, run_id, "db"),
            snapshot("d0000012", T0 + timedelta(hours=1), run_id, "db"),
        ],
        [
            snapshot("f0000012", T0 + timedelta(hours=1, minutes=5), run_id, "files"),
            snapshot("f0000011", T0 + timedelta(minutes=5), run_id, "files"),
        ],
    )
    for run in (None, str(run_id)):
        found = find_run(restic_repository(), "pw", run)
        assert (found.db.id, found.files.id) == ("d0000012", "f0000012")


def test_an_incomplete_files_run_is_restorable(backup: Backup, configured_folder: Path) -> None:
    # A run whose files step ended with restic exit 3 still has both snapshots.
    run_id = backup.run_id
    backup.snapshots(
        [snapshot("d0000001", T0, run_id, "db")],
        [snapshot("f0000001", T0 + timedelta(minutes=5), run_id, "files")],
    )
    assert find_run(restic_repository(), "pw", str(run_id)).run_id == run_id


@pytest.mark.parametrize("run", ["not-a-uuid", "pre-update-0.9.0"])
def test_unknown_run_is_refused(backup: Backup, configured_folder: Path, run: str) -> None:
    backup.snapshots(
        [snapshot("d0000001", T0, backup.run_id, "db")],
        [snapshot("f0000001", T0, backup.run_id, "files")],
    )
    for wanted in (run, str(uuid4())):
        with pytest.raises(BackupError) as caught:
            find_run(restic_repository(), "pw", wanted)
        assert caught.value.code == "run_not_found"


def test_a_run_with_only_a_database_snapshot_is_refused(
    backup: Backup, configured_folder: Path
) -> None:
    backup.snapshots([snapshot("d0000001", T0, backup.run_id, "db")], [])
    with pytest.raises(BackupError) as caught:
        find_run(restic_repository(), "pw", str(backup.run_id))
    assert caught.value.code == "run_not_found"
    with pytest.raises(BackupError):
        find_run(restic_repository(), "pw", None)


def restic_repository() -> restic.Repository:
    from apps.backup.destination import configured_repository

    return configured_repository()


# --- check_master_key_against_dump -----------------------------------------------------


def check(backup: Backup, master: str) -> None:
    db = restic.Snapshot(id="d0000001", time=T0, tags=(), paths=())
    check_master_key_against_dump(
        repository=restic_repository(),
        password="pw",
        db_snapshot=db,
        master_key=bytes.fromhex(master),
    )


def test_master_key_check_passes_with_the_right_key(backup: Backup) -> None:
    backup.contents()
    check(backup, MASTER)


def test_master_key_check_reads_columns_in_the_order_of_the_header(backup: Backup) -> None:
    wrapped = sealed_key(MASTER)
    keys = (
        "COPY public.workspaces_workspacekey (wrapped_key, workspace_id, id) FROM stdin;\n"
        f"\\\\x{wrapped.hex()}\t{WORKSPACE}\t{uuid4()}\n\\.\n"
    ).encode()
    backup.contents(keys=keys)
    check(backup, MASTER)
    with pytest.raises(BackupError) as caught:
        check(backup, OTHER_MASTER)
    assert caught.value.code == "wrong_master_key"


def test_a_backup_without_workspaces_passes_the_check(backup: Backup) -> None:
    backup.contents(keys=copy_block([]))
    check(backup, OTHER_MASTER)


def test_a_failing_dump_is_not_masked_by_pg_restore(backup: Backup) -> None:
    backup.contents()
    backup.restic.respond(
        ("dump", "--", "d0000001"), stdout=b"PGDMP trunc", stderr="read error", code=1
    )
    with pytest.raises(BackupError) as caught:
        check(backup, MASTER)
    assert caught.value.code == "restore_failed"


def test_a_wrong_password_on_the_dump_is_reported_as_such(backup: Backup) -> None:
    backup.contents()
    backup.restic.respond(("dump", "--", "d0000001"), stderr="wrong password", code=12)
    with pytest.raises(BackupError) as caught:
        check(backup, MASTER)
    assert caught.value.code == "wrong_password"


def test_a_hanging_dump_is_ended_when_pg_restore_fails(
    backup: Backup, tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import time

    hanging = tmp_path / "hanging-restic"
    hanging.write_text("#!/bin/sh\nexec sleep 60\n")
    hanging.chmod(0o755)
    settings.BRIEVIE_RESTIC_BINARY = str(hanging)
    started: list[subprocess.Popen[bytes]] = []
    real_popen = subprocess.Popen

    def popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = real_popen(*args, **kwargs)  # type: ignore[call-overload]
        started.append(process)
        return process  # type: ignore[no-any-return]

    monkeypatch.setattr("apps.backup.restore.subprocess.Popen", popen)
    backup.pg_restore.respond((), stderr="unexpected end of file", code=1)
    begin = time.monotonic()
    with pytest.raises(BackupError) as caught:
        check(backup, MASTER)
    assert caught.value.code == "restore_failed"
    assert time.monotonic() - begin < 30
    assert len(started) == 2
    assert all(process.poll() is not None for process in started)


def test_a_failing_pg_restore_stops(backup: Backup) -> None:
    backup.contents()
    backup.pg_restore.respond((), stderr="input file is too short", code=1)
    with pytest.raises(BackupError) as caught:
        check(backup, MASTER)
    assert caught.value.code == "restore_failed"


def test_output_without_the_key_table_is_refused(backup: Backup) -> None:
    backup.contents(keys=b"--\n-- PostgreSQL database dump complete\n--\n")
    with pytest.raises(BackupError) as caught:
        check(backup, MASTER)
    assert caught.value.code == "restore_failed"


def test_pg_restore_that_stops_reading_early_does_not_fail_the_dump(backup: Backup) -> None:
    # pg_restore may stop reading once it has the table; restic must still end cleanly.
    backup.contents()
    backup.restic.respond(
        ("dump", "--", "d0000001", restic.DB_FILENAME), stdout=b"x" * (4 * 1024 * 1024)
    )
    check(backup, MASTER)


def test_no_temporary_file_is_used(backup: Backup, monkeypatch: pytest.MonkeyPatch) -> None:
    # The dump streams from restic into pg_restore; nothing of it is put on disk.
    import tempfile

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a temporary file was made")

    for name in ("TemporaryFile", "NamedTemporaryFile", "SpooledTemporaryFile", "mkstemp"):
        monkeypatch.setattr(tempfile, name, refuse)
    backup.contents()
    check(backup, MASTER)


# --- restored_env ----------------------------------------------------------------------


def restore(text: str = ENV_COPY, **kwargs: object) -> str:
    options: dict[str, object] = {
        "master_key_hex": MASTER,
        "backup_password": DERIVED,
        "backup_dir_host": None,
    }
    options.update(kwargs)
    return restored_env(text, **options).text  # type: ignore[arg-type]


def test_restored_env_regenerates_the_superuser_and_owner_passwords() -> None:
    text = ENV_COPY + "POSTGRES_PASSWORD=old-superuser\nBRIEVIE_OWNER_PASSWORD=old-owner\n"
    first, second = env_values(restore(text)), env_values(restore(text))
    for key in ("POSTGRES_PASSWORD", "BRIEVIE_OWNER_PASSWORD"):
        assert re.fullmatch(r"[0-9a-f]{48}", first[key])
        assert first[key] != second[key]
        assert first[key] not in ("old-superuser", "old-owner")
    assert first["POSTGRES_PASSWORD"] != first["BRIEVIE_OWNER_PASSWORD"]
    owner = first["BRIEVIE_OWNER_PASSWORD"]
    assert first["DATABASE_OWNER_URL"] == f"postgres://brievie_owner:{owner}@localhost:5433/brievie"


def test_restored_env_uses_the_default_port_without_postgres_port() -> None:
    text = "BRIEVIE_HOSTNAME=brievie.example\n"
    values = env_values(restore(text))
    assert values["DATABASE_OWNER_URL"].endswith("@localhost:5432/brievie")


@pytest.mark.parametrize(
    "line",
    [
        "POSTGRES_PORT=5433 # host",
        "POSTGRES_PORT='5433'",
        'POSTGRES_PORT="5433"',
        "POSTGRES_PORT =  5433  ",
        "export POSTGRES_PORT=5433",
    ],
)
def test_restored_env_reads_the_port_as_compose_does(line: str) -> None:
    values = env_values(restore(f"BRIEVIE_HOSTNAME=x\n{line}\n"))
    assert values["DATABASE_OWNER_URL"].endswith("@localhost:5433/brievie")


@pytest.mark.parametrize("value", ["", "abc", "54x3", "0", "70000"])
def test_restored_env_refuses_a_port_it_cannot_read(value: str) -> None:
    with pytest.raises(BackupError) as caught:
        restore(f"POSTGRES_PORT={value}\n")
    assert caught.value.code == "restore_failed"


def test_restored_env_keeps_hostname_and_secret_key() -> None:
    restored = restore()
    values = env_values(restored)
    original = env_values(ENV_COPY)
    for key in (
        "SECRET_KEY",
        "BRIEVIE_HOSTNAME",
        "DATABASE_URL",
        "BRIEVIE_APP_PASSWORD",
        "BRIEVIE_BACKUP_DB_PASSWORD",
        "DATABASE_BACKUP_URL",
        "DEBUG",
    ):
        assert values[key] == original[key], key
    # The host lines (port, back-up folder) are this installation's, never the back-up's.
    assert "POSTGRES_PORT" not in values
    assert "BRIEVIE_BACKUP_DIR" not in values
    # Every other original line keeps its order, comments included; only the password
    # changed.
    host = ("POSTGRES_PORT=", "BRIEVIE_BACKUP_DIR=")
    kept = [
        line
        for line in ENV_COPY.splitlines()
        if not line.startswith(PASSWORD_LINE) and not line.startswith(host)
    ]
    restored_lines = restored.splitlines()
    assert [line for line in restored_lines if line in kept] == kept
    index = kept.index("BRIEVIE_HOSTNAME=brievie.example")
    password = restored_lines.index(f"BRIEVIE_BACKUP_PASSWORD={DERIVED}")
    assert restored_lines.index(kept[index]) < password
    assert restored.endswith("\n")


def test_restored_env_sets_the_master_key_once() -> None:
    restored = restore()
    assert env_values(restored)["BRIEVIE_MASTER_KEY"] == MASTER
    assert sum(1 for line in restored.splitlines() if line.startswith("BRIEVIE_MASTER_KEY=")) == 1
    assert "# BRIEVIE_MASTER_KEY=" in restored


def test_restored_env_points_at_the_new_backup_dir() -> None:
    values = env_values(restore(backup_dir_host="/media/usb-backup"))
    assert values["BRIEVIE_BACKUP_DIR"] == "/media/usb-backup"
    without = "BRIEVIE_HOSTNAME=brievie.example\n"
    assert env_values(restore(without, backup_dir_host="/media/x"))["BRIEVIE_BACKUP_DIR"] == (
        "/media/x"
    )


def test_restored_env_writes_the_backup_password_when_the_copy_has_none() -> None:
    text = "BRIEVIE_HOSTNAME=brievie.example\n# BRIEVIE_BACKUP_PASSWORD=\n"
    restored = restore(text)
    assert env_values(restored)["BRIEVIE_BACKUP_PASSWORD"] == DERIVED
    assert "# BRIEVIE_BACKUP_PASSWORD=" in restored


def test_restored_env_quotes_a_separate_password_with_special_characters() -> None:
    restored = restore(backup_password="pa$$ word#1")
    assert "BRIEVIE_BACKUP_PASSWORD='pa$$ word#1'" in restored.splitlines()


def test_restored_env_refuses_a_password_it_cannot_write() -> None:
    with pytest.raises(BackupError) as caught:
        restore(backup_password="it's\nbroken")
    assert caught.value.code == "backup_password_unwritable"
    assert "broken" not in str(caught.value)


@pytest.mark.parametrize("folder", ["/media/it's", "/media/a\nb"])
def test_restored_env_refuses_a_folder_it_cannot_write(folder: str) -> None:
    with pytest.raises(BackupError) as caught:
        restore(backup_dir_host=folder)
    assert caught.value.code == "backup_folder_unwritable"


SPELLINGS = [
    "{key}={v}",
    "export {key}={v}",
    "{key}: {v}",
    "'{key}'={v}",
    "{lower}={v}",
    "# {key}={v}",
    "  #  export {key}: {v}",
]


@pytest.mark.parametrize("key", EXCLUDED_KEYS)
@pytest.mark.parametrize("spelling", SPELLINGS)
def test_restored_env_drops_every_spelling_of_an_excluded_key(key: str, spelling: str) -> None:
    secret = "leaked-old-value-xyz"
    line = spelling.format(key=key, lower=key.lower(), v=secret)
    restored = restore(f"A=1\n{line}\nB=2\n")
    assert secret not in restored
    assert restored.startswith("A=1\nB=2\n")


# --- the command -----------------------------------------------------------------------


def test_restore_fetch_never_prints_the_key(
    backup: Backup,
    out_dir: Path,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backup.one_run()
    settings.BRIEVIE_MASTER_KEY = MASTER
    settings.BRIEVIE_BACKUP_PASSWORD = SEPARATE
    call_command("restore_fetch", "--out", str(out_dir))
    captured = capsys.readouterr()
    for secret in (MASTER, SEPARATE, DERIVED):
        assert secret not in captured.out
        assert secret not in captured.err
    lines = dict(line.split("=", 1) for line in captured.out.splitlines())
    assert lines == {
        "run_id": str(backup.run_id),
        "created_at": "2026-11-10T03:00:00+00:00",
        "hostname": "brievie.example",
        "brievie_version": "0.9.0",
    }
    assert env_values((out_dir / "env").read_text())["BRIEVIE_BACKUP_PASSWORD"] == SEPARATE


def test_restore_fetch_without_a_separate_password_derives_it(
    backup: Backup, out_dir: Path, settings: Settings
) -> None:
    backup.one_run()
    settings.BRIEVIE_MASTER_KEY = MASTER
    settings.BRIEVIE_BACKUP_PASSWORD = ""
    call_command("restore_fetch", "--out", str(out_dir), "--backup-dir-host", "/media/b")
    values = env_values((out_dir / "env").read_text())
    assert values["BRIEVIE_BACKUP_PASSWORD"] == DERIVED
    assert values["BRIEVIE_BACKUP_DIR"] == "/media/b"


@pytest.mark.parametrize(
    ("setup", "returncode", "sentence"),
    [
        ("wrong_password", 2, "Het wachtwoord van de back-up klopt niet."),
        ("wrong_master_key", 2, "De hoofdsleutel hoort niet bij deze back-up."),
        ("master_key_malformed", 2, None),
        ("unsafe_manifest", 1, "De inhoudsopgave van de back-up kon niet worden gelezen."),
        ("newer_backup", 3, "Deze back-up komt van een nieuwere versie van Brievie."),
        ("run_not_found", 1, "Deze back-up is niet gevonden."),
    ],
)
def test_restore_fetch_exit_codes(
    backup: Backup,
    out_dir: Path,
    settings: Settings,
    setup: str,
    returncode: int,
    sentence: str | None,
) -> None:
    settings.BRIEVIE_MASTER_KEY = MASTER
    settings.BRIEVIE_BACKUP_PASSWORD = ""
    if setup == "wrong_password":
        backup.restic.respond(("snapshots",), stderr="wrong password", code=12)
    elif setup == "wrong_master_key":
        backup.one_run(keys=copy_block([(WORKSPACE, sealed_key(OTHER_MASTER))]))
    elif setup == "master_key_malformed":
        settings.BRIEVIE_MASTER_KEY = "not-a-key"
        backup.one_run()
    elif setup == "unsafe_manifest":
        unsafe = json.loads(manifest_json(backup.run_id))
        unsafe["hostname"] = "brievie.example\nrun_id=forged"
        backup.one_run(manifest=json.dumps(unsafe).encode())
    elif setup == "newer_backup":
        backup.one_run(manifest=manifest_json(backup.run_id, migrations={"x": ["9999_future"]}))
    else:
        backup.snapshots([], [])
    with pytest.raises(CommandError) as caught:
        call_command("restore_fetch", "--out", str(out_dir))
    assert caught.value.returncode == returncode
    if sentence is not None:
        assert str(caught.value) == sentence
    assert MASTER not in str(caught.value)
    assert list(out_dir.iterdir()) == []


def test_restore_fetch_reads_the_owner(
    backup: Backup, out_dir: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup.one_run()
    settings.BRIEVIE_MASTER_KEY = MASTER
    settings.BRIEVIE_BACKUP_PASSWORD = ""
    calls = record_fchown(monkeypatch)
    call_command("restore_fetch", "--out", str(out_dir), "--out-owner", "501:20")
    assert [(uid, gid) for _inode, uid, gid in calls] == [(501, 20), (501, 20)]
    with pytest.raises(CommandError):
        call_command("restore_fetch", "--out", str(out_dir), "--out-owner", "me")


def test_restored_env_drops_every_host_line_and_says_which() -> None:
    # Ports carry a port (the owner URL is still built from it); the rest a marker.
    lines = [f"{key}={'5433' if 'PORT' in key else 'from-the-backup'}" for key in HOST_KEYS]
    text = ENV_COPY + "\n".join(["# BRIEVIE_IMAGE=a comment stays", *lines]) + "\n"

    restored = restored_env(
        text, master_key_hex=MASTER, backup_password=DERIVED, backup_dir_host=None
    )

    values = env_values(restored.text)
    for key in HOST_KEYS:
        assert key not in values, key
    assert "from-the-backup" not in restored.text
    assert "/mnt/old-disk" not in restored.text
    assert "# BRIEVIE_IMAGE=a comment stays" in restored.text
    assert set(restored.dropped) == set(HOST_KEYS)


@pytest.mark.parametrize("spelling", ["export {key}=x", "{lower}=x", "'{key}'=x", "{key}: x"])
def test_restored_env_drops_a_host_line_in_any_spelling(spelling: str) -> None:
    line = spelling.format(key="BRIEVIE_IMAGE", lower="brievie_image")
    restored = restored_env(
        ENV_COPY + line + "\n", master_key_hex=MASTER, backup_password=DERIVED, backup_dir_host=None
    )
    assert line not in restored.text
    assert "BRIEVIE_IMAGE" in restored.dropped


def test_an_explicit_backup_folder_is_still_written_and_not_reported() -> None:
    restored = restored_env(
        ENV_COPY, master_key_hex=MASTER, backup_password=DERIVED, backup_dir_host="/media/new"
    )
    assert env_values(restored.text)["BRIEVIE_BACKUP_DIR"] == "/media/new"
    assert "BRIEVIE_BACKUP_DIR" not in restored.dropped
    assert restored.dropped == ("POSTGRES_PORT",)


def test_restore_fetch_says_which_settings_it_left_out(
    backup: Backup,
    out_dir: Path,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backup.one_run()
    settings.BRIEVIE_MASTER_KEY = MASTER
    settings.BRIEVIE_BACKUP_PASSWORD = ""
    call_command("restore_fetch", "--out", str(out_dir))
    captured = capsys.readouterr()
    assert "run_id=" in captured.out
    assert "niet uit de back-up overgenomen" not in captured.out  # stdout stays key=value
    assert captured.err.strip().splitlines() == [
        "Deze instellingen zijn niet uit de back-up overgenomen; deze installatie zet ze "
        "zelf: POSTGRES_PORT, BRIEVIE_BACKUP_DIR."
    ]


@pytest.mark.parametrize(
    "spelling",
    ["{key}=x", "export {key}=x", "{lower}=x", "'{key}'=x", '"{key}"=x', "{key}: x", "{key} = x"],
)
@pytest.mark.parametrize(
    "key", ["COMPOSE_PROFILES", "COMPOSE_FILE", "DOCKER_HOST", "DOCKER_CONTEXT", "COMPOSE_X_Y"]
)
def test_restored_env_drops_every_compose_and_docker_key(spelling: str, key: str) -> None:
    line = spelling.format(key=key, lower=key.lower())
    restored = restored_env(
        ENV_COPY + line + "\n", master_key_hex=MASTER, backup_password=DERIVED, backup_dir_host=None
    )
    assert line not in restored.text
    assert key not in env_values(restored.text)
    assert key in restored.dropped


def test_a_key_that_only_starts_like_compose_is_kept() -> None:
    restored = restored_env(
        ENV_COPY + "MY_COMPOSE_NOTE=keep\nDOCKERFILE_NOTE=keep\n",
        master_key_hex=MASTER,
        backup_password=DERIVED,
        backup_dir_host=None,
    )
    values = env_values(restored.text)
    assert values["MY_COMPOSE_NOTE"] == "keep"
    assert values["DOCKERFILE_NOTE"] == "keep"

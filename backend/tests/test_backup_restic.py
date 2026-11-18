import json
import shutil
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest

from apps.backup import restic
from apps.backup.errors import REASONS, BackupError
from apps.backup.restic import Repository, ResticResult
from tests.backup_support import FakeBinary

RUN = uuid.UUID("11111111-2222-3333-4444-555555555555")


def test_dump_is_streamed_with_run_and_kind_tags() -> None:
    command = ["pg_dump", "--format=custom", "--no-password"]
    assert restic.backup_stdin_argv(RUN, command) == [
        "backup",
        "--json",
        "--host",
        "brievie",
        "--tag",
        f"run:{RUN}",
        "--tag",
        "kind:db",
        "--stdin-from-command",
        "--stdin-filename",
        "brievie.pgdump",
        "--",
        "pg_dump",
        "--format=custom",
        "--no-password",
    ]


def test_files_backup_excludes_temporary_files() -> None:
    argv = restic.backup_files_argv(RUN, ["/data/documents", "/data/config"])
    assert argv == [
        "backup",
        "--json",
        "--host",
        "brievie",
        "--tag",
        f"run:{RUN}",
        "--tag",
        "kind:files",
        "--exclude",
        ".tmp-*",
        "--",
        "/data/documents",
        "/data/config",
    ]


def test_forget_run_removes_only_the_snapshots_of_one_run() -> None:
    run_id = uuid.uuid4()
    argv = restic.forget_run_argv(run_id)
    # restic 0.18 refuses `forget --tag` without a policy ("no policy was specified");
    # --unsafe-allow-remove-all removes every snapshot that matches the filters, so the
    # filters must always be there: this host and exactly this run's tag.
    assert argv == [
        "forget",
        "--unsafe-allow-remove-all",
        "--host",
        restic.HOST,
        "--tag",
        f"run:{run_id}",
    ]
    assert "--prune" not in argv


def test_forget_prunes_with_keep_within_and_never_groups_by_tags() -> None:
    argv = restic.forget_argv()
    assert argv == [
        "forget",
        "--prune",
        "--host",
        "brievie",
        "--group-by",
        "host,paths",
        # A safety back-up of a restore outlives ordinary retention.
        "--keep-tag",
        "kind:safety",
        # Every snapshot of the last two days stays: the daily rule alone keeps one per
        # day, so a back-up made later the same day removed the earlier one (CI found a
        # safety back-up before a restore pruning the run being restored).
        "--keep-within",
        "2d",
        "--keep-within-daily",
        "7d",
        "--keep-within-weekly",
        "1m",
        "--keep-within-monthly",
        "1y",
    ]
    assert "tags" not in argv[argv.index("--group-by") + 1]


def test_check_reads_a_sample() -> None:
    assert restic.check_argv() == ["check", "--read-data-subset", "5%"]


def test_other_argv_builders() -> None:
    assert restic.init_argv() == ["init"]
    assert restic.unlock_argv() == ["unlock"]
    assert restic.cat_config_argv() == ["cat", "config"]
    assert restic.snapshots_argv("run:x") == ["snapshots", "--json", "--tag", "run:x"]
    assert restic.dump_argv("abcdef12", "/brievie.pgdump") == [
        "dump",
        "--",
        "abcdef12",
        "/brievie.pgdump",
    ]
    assert restic.restore_argv("abcdef12", "/t", ["/a", "/b"]) == [
        "restore",
        "--target",
        "/t",
        "--exclude-xattr",
        "*",
        "--include",
        "/a",
        "--include",
        "/b",
        "--",
        "abcdef12",
    ]


@pytest.mark.parametrize(
    "snapshot_id", ["abc", "--target=/", "-x", "ABCDEF12", "abcdef12 /", "g" * 8, "a" * 65, ""]
)
def test_a_snapshot_id_that_restic_would_not_print_is_refused(snapshot_id: str) -> None:
    with pytest.raises(BackupError) as caught:
        restic.dump_argv(snapshot_id, "/brievie.pgdump")
    assert caught.value.code == "restore_failed"
    with pytest.raises(BackupError) as caught:
        restic.restore_argv(snapshot_id, "/", ["/data/documents"])
    assert caught.value.code == "restore_failed"


@pytest.mark.parametrize("include", ["/data/docu*", "/data/?", "/data/[a]", "relative", "/a\\*"])
def test_a_restore_include_with_a_glob_or_relative_path_is_refused(include: str) -> None:
    with pytest.raises(BackupError) as caught:
        restic.restore_argv("abcdef12", "/", ["/data/documents", include])
    assert caught.value.code == "unsafe_path"


@pytest.mark.parametrize(
    "value",
    ["rest:https://u:p@h/r", "rest:https://u@h/r", "s3:https://k:s@h/b", "rest:http://:p@h/r"],
)
def test_repository_with_credentials_is_refused(value: str) -> None:
    with pytest.raises(BackupError) as caught:
        restic.check_repository_value(value)
    assert caught.value.code == "repository_has_credentials"


@pytest.mark.parametrize(
    "value",
    [
        "rest:https://h:8000/r",
        "s3:https://s3.example.com/bucket/brievie",
        "s3:s3.amazonaws.com/bucket",
        "rest:https://h/r/a@b",
    ],
)
def test_repository_without_credentials_is_accepted(value: str) -> None:
    restic.check_repository_value(value)


@pytest.mark.parametrize("value", ["sftp:u@h:/p", "/mnt/usb", "b2:bucket", ""])
def test_unsupported_repository_is_refused(value: str) -> None:
    with pytest.raises(BackupError) as caught:
        restic.check_repository_value(value)
    assert caught.value.code == "repository_unsupported"


def test_redact_removes_secrets_and_userinfo() -> None:
    text = "Fatal: unable to open rest:https://anne:geheim@nas:8000/brievie/ with pw S3CR3T"
    out = restic.redact(text, ["S3CR3T", "geheim", ""])
    assert "geheim" not in out
    assert "S3CR3T" not in out
    assert "anne" not in out
    assert "rest:https://***@nas:8000/brievie/" in out


def test_redact_cuts_long_text() -> None:
    assert len(restic.redact("x" * 5000, [])) == 2000


def test_env_contains_only_what_restic_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIEVIE_MASTER_KEY", "must-not-leak")
    repository = Repository("s3", "s3:https://h/b", {"AWS_ACCESS_KEY_ID": "k"})
    env = restic.restic_env(repository, "pw", extra={"PGHOST": "db"})
    assert set(env) == {
        "PATH",
        "HOME",
        "RESTIC_REPOSITORY",
        "RESTIC_PASSWORD",
        "AWS_ACCESS_KEY_ID",
        "PGHOST",
        "TZ",
    }
    # --time is read in the process's time zone: always UTC, as the run's times are.
    assert env["TZ"] == "UTC"
    assert env["RESTIC_REPOSITORY"] == "s3:https://h/b"
    assert env["RESTIC_PASSWORD"] == "pw"


@pytest.mark.parametrize(
    ("code", "stderr", "expected"),
    [
        (10, "", "repository_missing"),
        (11, "", "repository_locked"),
        (12, "", "wrong_password"),
        (3, "", "files_incomplete"),
        (1, "write: No space left on device", "destination_full"),
        (1, "dial tcp: connect: connection refused", "destination_unreachable"),
        (1, "lookup nas: no such host", "destination_unreachable"),
        (1, "read: i/o timeout", "destination_unreachable"),
        (1, "something else", "restic_failed"),
        (2, "", "restic_failed"),
    ],
)
def test_exit_codes_are_classified(code: int, stderr: str, expected: str) -> None:
    result = ResticResult(returncode=code, stdout=b"", stderr=stderr)
    assert restic.classify(result) == expected
    assert expected in REASONS
    with pytest.raises(BackupError) as caught:
        restic.raise_for(result)
    assert caught.value.code == expected


def test_zero_exit_does_not_raise() -> None:
    restic.raise_for(ResticResult(returncode=0, stdout=b"", stderr=""))


def test_exit_codes_come_from_the_binary(fake_restic: FakeBinary) -> None:
    fake_restic.respond(("check",), code=12, stderr="wrong password")
    fake_restic.respond(("init",), code=0)
    repository = Repository("folder", "/repo", {})
    env = restic.restic_env(repository, "pw")
    assert restic.run(restic.init_argv(), env, secrets=["pw"]).returncode == 0
    result = restic.run(restic.check_argv(), env, secrets=["pw"])
    assert restic.classify(result) == "wrong_password"
    assert fake_restic.calls == [["init"], ["check", "--read-data-subset", "5%"]]


def test_run_redacts_stderr(fake_restic: FakeBinary) -> None:
    fake_restic.respond(
        ("snapshots",),
        stdout=b"[]",
        stderr="Fatal: password S3CR3T for rest:https://anne:geheim@nas/r",
        code=1,
    )
    result = restic.run(
        ["snapshots"],
        restic.restic_env(Repository("folder", "/r", {}), "S3CR3T"),
        secrets=["S3CR3T"],
    )
    assert result.stdout == b"[]"
    assert "S3CR3T" not in result.stderr
    assert "geheim" not in result.stderr
    assert "***" in result.stderr


def test_newest_response_rule_wins(fake_restic: FakeBinary) -> None:
    fake_restic.respond((), code=5)
    fake_restic.respond(("check",), code=7)
    env = restic.restic_env(Repository("folder", "/r", {}), "pw")
    assert restic.run(["check", "x"], env, secrets=[]).returncode == 7
    assert restic.run(["other"], env, secrets=[]).returncode == 5


def test_no_secret_is_ever_in_an_argv(fake_restic: FakeBinary) -> None:
    password = "PW-xyz-123"
    credentials = {"AWS_ACCESS_KEY_ID": "AKIAKEY123", "AWS_SECRET_ACCESS_KEY": "SECRETKEY456"}
    libpq = {"PGPASSWORD": "dbpass789", "PGUSER": "brievie_backup"}
    secrets = [password, *credentials.values(), libpq["PGPASSWORD"]]
    repository = Repository("s3", "s3:https://s3.example.com/bucket/brievie", credentials)
    restic.check_repository_value(repository.location)

    builders = [
        restic.init_argv(),
        restic.unlock_argv(),
        restic.backup_stdin_argv(RUN, ["pg_dump", "--format=custom", "--no-password"]),
        restic.backup_files_argv(RUN, ["/data/documents"]),
        restic.forget_argv(),
        restic.check_argv(),
        restic.snapshots_argv(f"run:{RUN}"),
        restic.dump_argv("abcdef12", "/brievie.pgdump"),
        restic.restore_argv("abcdef12", "/target", ["/data/documents"]),
    ]
    env = restic.restic_env(repository, password, extra=libpq)
    for argv in builders:
        restic.run(argv, env, secrets=secrets)

    assert len(fake_restic.calls) == len(builders)
    for call in fake_restic.calls:
        for secret in secrets:
            assert all(secret not in argument for argument in call)
    for keys in fake_restic.env_keys:
        assert {
            "RESTIC_PASSWORD",
            "RESTIC_REPOSITORY",
            "AWS_SECRET_ACCESS_KEY",
            "PGPASSWORD",
        } <= set(keys)


def test_run_hands_the_dump_command_to_the_fake_tool(
    fake_tool: Callable[[str], FakeBinary],
) -> None:
    pg_dump = fake_tool("pg_dump")
    pg_dump.respond(("--format=custom",), stdout=b"bytes")
    found = shutil.which("pg_dump")
    assert found == str(pg_dump.path)
    done = subprocess.run(  # noqa: S603
        [found, "--format=custom", "--no-password"], capture_output=True, check=False
    )
    assert done.stdout == b"bytes"
    assert pg_dump.calls == [["--format=custom", "--no-password"]]


def test_summary_bytes_reads_the_summary_line() -> None:
    stdout = b"\n".join(
        [
            json.dumps({"message_type": "status", "total_bytes": 1}).encode(),
            b"not json at all",
            json.dumps({"message_type": "summary", "total_bytes_processed": 4242}).encode(),
        ]
    )
    assert restic.summary_bytes(stdout) == 4242


def test_summary_bytes_without_summary_fails() -> None:
    with pytest.raises(BackupError) as caught:
        restic.summary_bytes(b'{"message_type": "status"}\n')
    assert caught.value.code == "restic_failed"


def test_parse_snapshots_newest_first() -> None:
    payload = json.dumps(
        [
            {
                "id": "old",
                "time": "2026-11-16T10:00:00.123456789+01:00",
                "tags": ["run:a", "kind:db"],
                "paths": ["/brievie.pgdump"],
            },
            {
                "id": "new",
                "time": "2026-11-17T10:00:00Z",
                "tags": ["run:b"],
                "paths": ["/data"],
            },
        ]
    ).encode()
    snapshots = restic.parse_snapshots(payload)
    assert [s.id for s in snapshots] == ["new", "old"]
    assert snapshots[1].tags == ("run:a", "kind:db")
    assert snapshots[1].paths == ("/brievie.pgdump",)
    assert snapshots[1].time.tzinfo is not None


def test_parse_snapshots_of_nothing() -> None:
    assert restic.parse_snapshots(b"null") == []


@pytest.mark.parametrize(
    "value",
    ["s3:key:secret@host/bucket", "rest:u@h/r", "s3:https://k:p@ss@h/b"],
)
def test_repository_userinfo_is_refused_with_or_without_scheme_separator(value: str) -> None:
    with pytest.raises(BackupError) as caught:
        restic.check_repository_value(value)
    assert caught.value.code == "repository_has_credentials"


def test_redact_hides_a_password_that_contains_an_at_sign() -> None:
    out = restic.redact("open rest:https://anne:p@ss@nas/r failed", [])
    assert "ss" not in out.replace("failed", "")
    assert "anne" not in out
    assert "rest:https://***@nas/r" in out


def test_redact_hides_userinfo_without_a_scheme_separator() -> None:
    out = restic.redact("Fatal: s3:key:secret@host/bucket unreachable", [])
    assert "key" not in out
    assert "secret" not in out
    assert "s3:***@host/bucket" in out


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b'{"id": "a"}',
        b"[1]",
        b'[{"id": "a"}]',
        b'[{"time": "2026-11-17T10:00:00Z"}]',
        b'[{"id": "a", "time": "yesterday"}]',
    ],
)
def test_parse_snapshots_of_garbage_fails_as_restic_failed(payload: bytes) -> None:
    with pytest.raises(BackupError) as caught:
        restic.parse_snapshots(payload)
    assert caught.value.code == "restic_failed"


def test_the_reading_builders_can_skip_the_lock_for_a_read_only_repository() -> None:
    # A restore reads from a repository that may be mounted read-only: no lock file.
    assert restic.snapshots_argv("run:x", no_lock=True) == [
        "snapshots",
        "--no-lock",
        "--json",
        "--tag",
        "run:x",
    ]
    assert restic.dump_argv("abcdef12", "/brievie.pgdump", no_lock=True) == [
        "dump",
        "--no-lock",
        "--",
        "abcdef12",
        "/brievie.pgdump",
    ]
    assert restic.restore_argv("abcdef12", "/", ["/data/documents"], no_lock=True) == [
        "restore",
        "--no-lock",
        "--target",
        "/",
        "--exclude-xattr",
        "*",
        "--include",
        "/data/documents",
        "--",
        "abcdef12",
    ]
    assert restic.cat_config_argv(no_lock=True) == ["cat", "--no-lock", "config"]


def test_the_writing_and_locking_builders_never_skip_the_lock() -> None:
    run_id = uuid.uuid4()
    for argv in (
        restic.init_argv(),
        restic.unlock_argv(),
        restic.backup_stdin_argv(run_id, ["pg_dump"]),
        restic.backup_files_argv(run_id, ["/data/documents"]),
        restic.forget_argv(),
        restic.forget_run_argv(run_id),
        restic.check_argv(),
        restic.snapshots_argv("run:x"),
        restic.dump_argv("abcdef12", "/x"),
        restic.restore_argv("abcdef12", "/", ["/data/documents"]),
        restic.cat_config_argv(),
    ):
        assert "--no-lock" not in argv, argv


AT = datetime(2026, 11, 17, 23, 59, 58, tzinfo=UTC)


def test_both_halves_of_a_run_carry_the_runs_own_time_and_host() -> None:
    # One run, one time: retention can never keep one half of a run and drop the other.
    local = AT.astimezone(timezone(timedelta(hours=1)))  # 00:59:58 the next day, locally
    db = restic.backup_stdin_argv(RUN, ["pg_dump"], host="brievie-0123456789ab", at=local)
    files = restic.backup_files_argv(
        RUN, ["/data/documents"], host="brievie-0123456789ab", at=local
    )
    for argv in (db, files):
        assert argv[argv.index("--host") + 1] == "brievie-0123456789ab"
        assert argv[argv.index("--time") + 1] == "2026-11-17 23:59:58"
        assert argv.index("--time") < argv.index("--")
        assert "kind:safety" not in argv


def test_a_safety_backup_tags_both_halves() -> None:
    db = restic.backup_stdin_argv(RUN, ["pg_dump"], at=AT, safety=True)
    files = restic.backup_files_argv(RUN, ["/data/documents"], at=AT, safety=True)
    for argv in (db, files):
        tagged = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--tag"]
        assert tagged == [f"run:{RUN}", f"kind:{'db' if argv is db else 'files'}", "kind:safety"]


def test_the_files_backup_leaves_out_the_given_paths() -> None:
    argv = restic.backup_files_argv(
        RUN,
        ["/data/backup-config"],
        excludes=["/data/backup-config/installation-id", "/data/backup-config/installation-id.tmp"],
    )
    excluded = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--exclude"]
    assert excluded == [
        ".tmp-*",
        "/data/backup-config/installation-id",
        "/data/backup-config/installation-id.tmp",
    ]
    assert argv[-2:] == ["--", "/data/backup-config"]


def test_retention_and_run_removal_stay_on_the_own_host() -> None:
    assert restic.forget_argv("brievie-0123456789ab")[2:4] == ["--host", "brievie-0123456789ab"]
    assert restic.forget_run_argv(RUN, "brievie-0123456789ab") == [
        "forget",
        "--unsafe-allow-remove-all",
        "--host",
        "brievie-0123456789ab",
        "--tag",
        f"run:{RUN}",
    ]
    assert restic.snapshots_host_argv("brievie-0123456789ab") == [
        "snapshots",
        "--json",
        "--host",
        "brievie-0123456789ab",
    ]


def test_a_restore_never_restores_extended_attributes() -> None:
    argv = restic.restore_argv("abcdef12", "/", ["/data/documents"], no_lock=True)
    assert argv[argv.index("--exclude-xattr") + 1] == "*"
    assert argv.index("--exclude-xattr") < argv.index("--")

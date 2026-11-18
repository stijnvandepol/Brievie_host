"""End-to-end check against the real restic binary (>= 0.17).

Skipped where restic is not installed; the image and CI's restore job run it.
"""

import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.backup import restic
from apps.backup.restic import Repository


@pytest.fixture(autouse=True)
def _restic_installed() -> None:
    """In CI restic must be there (the backend job installs the image's binary): a
    missing one fails instead of skipping, as the ShellCheck test does."""
    if shutil.which("restic") is None:
        if os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail("restic must be installed in CI")
        pytest.skip("restic is not installed")


PASSWORD = "integration-test-password"


def _run(argv: list[str], env: dict[str, str], *, ok: bool = True) -> restic.ResticResult:
    result = restic.run(argv, env, secrets=[PASSWORD])
    if ok:
        assert result.returncode == 0, result.stderr
    return result


def test_restic_round_trip(tmp_path: Path, settings: object) -> None:
    settings.BRIEVIE_RESTIC_BINARY = shutil.which("restic")  # type: ignore[attr-defined]
    repository = Repository("folder", str(tmp_path / "repo"), {})
    env = restic.restic_env(repository, PASSWORD)
    run_id = uuid.uuid4()

    _run(restic.init_argv(), env)
    _run(restic.backup_stdin_argv(run_id, ["sh", "-c", "printf dump-bytes"]), env)

    data = tmp_path / "data"
    data.mkdir()
    (data / "a.page").write_bytes(b"page")
    (data / ".tmp-x").write_bytes(b"partial")
    files = _run(restic.backup_files_argv(run_id, [str(data)]), env)
    assert restic.summary_bytes(files.stdout) > 0

    listed = _run(restic.snapshots_argv(f"run:{run_id}"), env)
    snapshots = restic.parse_snapshots(listed.stdout)
    assert len(snapshots) == 2

    db = next(s for s in snapshots if "kind:db" in s.tags)
    dumped = _run(restic.dump_argv(db.id, f"/{restic.DB_FILENAME}"), env)
    assert dumped.stdout == b"dump-bytes"

    file_snapshot = next(s for s in snapshots if "kind:files" in s.tags)
    contents = _run(["ls", file_snapshot.id], env).stdout.decode()
    assert "a.page" in contents
    assert ".tmp-x" not in contents

    _run(restic.forget_argv(), env)
    _run(restic.check_argv(), env)


def test_retention_keeps_every_run_of_the_last_two_days(tmp_path: Path, settings: object) -> None:
    # Three back-ups within a minute (a safety back-up right before a restore is one of
    # them): retention must keep all three, database and files, not one per day.
    settings.BRIEVIE_RESTIC_BINARY = shutil.which("restic")  # type: ignore[attr-defined]
    repository = Repository("folder", str(tmp_path / "repo"), {})
    env = restic.restic_env(repository, PASSWORD)
    _run(restic.init_argv(), env)
    data = tmp_path / "data"
    data.mkdir()
    runs = [uuid.uuid4() for _ in range(3)]
    for number, run_id in enumerate(runs):
        (data / f"{number}.page").write_bytes(b"page")
        _run(restic.backup_stdin_argv(run_id, ["sh", "-c", f"printf dump-{number}"]), env)
        _run(restic.backup_files_argv(run_id, [str(data)]), env)

    _run(restic.forget_argv(), env)

    for run_id in runs:
        listed = _run(restic.snapshots_argv(f"run:{run_id}", no_lock=True), env)
        kinds = sorted(tag for s in restic.parse_snapshots(listed.stdout) for tag in s.tags)
        assert kinds == ["kind:db", "kind:files", f"run:{run_id}", f"run:{run_id}"], run_id


def _repository(tmp_path: Path, settings: object) -> dict[str, str]:
    settings.BRIEVIE_RESTIC_BINARY = shutil.which("restic")  # type: ignore[attr-defined]
    env = restic.restic_env(Repository("folder", str(tmp_path / "repo"), {}), PASSWORD)
    _run(restic.init_argv(), env)
    return env


def _back_up(
    env: dict[str, str], data: Path, run_id: uuid.UUID, at: datetime, *, safety: bool = False
) -> None:
    # The files snapshot is made a moment after the dump; both carry the run's start.
    _run(restic.backup_stdin_argv(run_id, ["sh", "-c", "printf dump"], at=at, safety=safety), env)
    _run(restic.backup_files_argv(run_id, [str(data)], at=at, safety=safety), env)


def _kinds_by_run(env: dict[str, str]) -> dict[str, set[str]]:
    listed = restic.parse_snapshots(_run(["snapshots", "--json"], env).stdout)
    runs: dict[str, set[str]] = {}
    for snapshot in listed:
        run = next(tag for tag in snapshot.tags if tag.startswith("run:"))
        runs.setdefault(run, set()).update(t for t in snapshot.tags if t.startswith("kind:"))
    return runs


def test_retention_across_midnight_keeps_only_whole_runs(tmp_path: Path, settings: object) -> None:
    # Runs just before and after midnight, over three weeks and a month boundary: the day,
    # week and month rules must pick the same run in the database group and the files
    # group, so every kept run has both halves.
    env = _repository(tmp_path, settings)
    data = tmp_path / "data"
    data.mkdir()
    start = datetime(2026, 10, 15, 23, 59, 58, tzinfo=UTC)
    moments = []
    for day in range(25):
        moments.append(start + timedelta(days=day))
        moments.append(start + timedelta(days=day, seconds=4))  # 00:00:02 the next day
    for number, moment in enumerate(moments):
        (data / f"{number}.page").write_bytes(b"page")
        _back_up(env, data, uuid.uuid4(), moment)

    _run(restic.forget_argv(), env)

    runs = _kinds_by_run(env)
    assert len(runs) < len(moments)  # retention did remove runs
    assert all(kinds == {"kind:db", "kind:files"} for kinds in runs.values()), runs


def test_ordinary_retention_never_removes_a_safety_backup(tmp_path: Path, settings: object) -> None:
    env = _repository(tmp_path, settings)
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.page").write_bytes(b"page")
    safety = uuid.uuid4()
    first = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    _back_up(env, data, safety, first, safety=True)
    for day in range(1, 20):
        _back_up(env, data, uuid.uuid4(), first + timedelta(days=day))

    _run(restic.forget_argv(), env)

    assert _kinds_by_run(env)[f"run:{safety}"] == {"kind:db", "kind:files", restic.SAFETY_TAG}

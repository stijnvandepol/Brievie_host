"""End-to-end check against the real restic binary (>= 0.17).

Skipped where restic is not installed; the image and CI's restore job run it.
"""

import shutil
import uuid
from pathlib import Path

import pytest

from apps.backup import restic
from apps.backup.restic import Repository

pytestmark = pytest.mark.skipif(shutil.which("restic") is None, reason="restic is not installed")

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

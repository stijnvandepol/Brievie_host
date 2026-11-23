"""The CI job `update` and the throwaway releases it makes (update-releases.py).

The job runs only on a GitHub runner. These tests read it as text and run the release
maker against a git repository in tmp_path; nothing here starts docker.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
MAKER = ROOT / ".github" / "scripts" / "update-releases.py"
JOB = re.search(r"^  update:\n(.*?)(?=^  [\w-]+:\n|\Z)", CI, re.M | re.S)
assert JOB
UPDATE = JOB.group(0)
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
}


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests use git and files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env={**GIT_ENV, "HOME": str(cwd)},
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    upstream = tmp_path / "upstream"
    (upstream / "backend" / "apps" / "updates" / "migrations").mkdir(parents=True)
    (upstream / "deploy").mkdir()
    files = (
        "backend/pyproject.toml",
        "backend/uv.lock",
        "deploy/release.json",
        "CHANGELOG.md",
        "backend/apps/updates/migrations/__init__.py",
        "backend/apps/updates/migrations/0001_initial.py",
    )
    for name in files:
        shutil.copy(ROOT / name, upstream / name)
    git(upstream, "init", "-q", "-b", "main")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-q", "-m", "base")
    clone = tmp_path / "source"
    git(tmp_path, "clone", "-q", str(upstream), str(clone))
    return clone


def make(source: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(MAKER), str(source)],
        env={**GIT_ENV, "HOME": str(source.parent)},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def version_at(source: Path, tag: str, path: str) -> str:
    return git(source, "show", f"{tag}:{path}")


def test_two_releases_on_top_of_the_installed_one(source: Path) -> None:
    done = make(source)
    assert done.returncode == 0, done.stderr
    lines = dict(line.split("=", 1) for line in done.stdout.splitlines())
    installed, good, bad = lines["installed"], lines["good"], lines["bad"]
    major, minor, patch = (int(part) for part in installed.split("."))
    assert good == f"{major}.{minor}.{patch + 1}"
    assert bad == f"{major}.{minor}.{patch + 2}"
    for tag, version, minimum in ((good, good, installed), (bad, bad, good)):
        release = json.loads(version_at(source, f"v{tag}", "deploy/release.json"))
        assert (release["version"], release["min_upgrade_from"]) == (version, minimum)
        assert f'version = "{version}"' in version_at(source, f"v{tag}", "backend/pyproject.toml")
        lock = version_at(source, f"v{tag}", "backend/uv.lock")
        assert f'name = "brievie"\nversion = "{version}"' in lock
        changelog = version_at(source, f"v{tag}", "CHANGELOG.md")
        assert re.findall(r"^## \[(\d[^\]]*)\]", changelog, re.M)[0] == version
    passing = "backend/apps/updates/migrations/0002_ci_ok.py"
    failing = "backend/apps/updates/migrations/0003_ci_fails.py"
    assert "RuntimeError" not in version_at(source, f"v{bad}", passing)
    assert 'dependencies = [("updates", "0001_initial")]' in version_at(source, f"v{bad}", passing)
    assert "RuntimeError" in version_at(source, f"v{bad}", failing)
    assert 'dependencies = [("updates", "0002_ci_ok")]' in version_at(source, f"v{bad}", failing)
    for name in (passing, failing):
        with pytest.raises(subprocess.CalledProcessError):
            version_at(source, f"v{good}", name)


def test_the_throwaway_migrations_compile() -> None:
    text = MAKER.read_text()
    body = text.split("MIGRATION_TEMPLATE = '''", 1)[1].split("'''", 1)[0]
    for doc, code in (("passes", "return None"), ("fails", "raise RuntimeError('x')")):
        source = body % {"doc": doc, "body": code, "previous": "0001_initial"}
        compile(source, "migration.py", "exec")


def test_a_clone_of_a_remote_repository_is_refused(source: Path) -> None:
    git(source, "remote", "set-url", "origin", "https://example.invalid/brievie.git")
    done = make(source)
    assert done.returncode == 1
    assert git(source, "tag", "-l") == ""


# --- the job ----------------------------------------------------------------------------


def test_the_job_is_code_only_github_hosted_and_bounded() -> None:
    header = UPDATE.split("steps:", 1)[0]
    assert "!= 'schedule'" in header
    assert "timeout-minutes:" in header
    assert "permissions:" not in header  # the workflow's contents: read
    assert UPDATE.index('test "$RUNNER_ENVIRONMENT" = github-hosted') < UPDATE.index("./brievie")
    assert "persist-credentials: false" in UPDATE


def test_the_way_back_is_typed_as_printed() -> None:
    assert 'grep -qx "  git checkout v$GOOD"' in UPDATE
    printed = '"  ./brievie restore pre-update-$BAD --replace --skip-safety-backup"'
    assert f"grep -qx {printed}" in UPDATE
    assert 'checkout -q "v$GOOD"' in UPDATE
    assert './brievie restore "pre-update-$BAD" --replace --skip-safety-backup' in UPDATE
    assert 'test "$rc" = 3' in UPDATE
    # The runner runs bash with -e: a failing command ends the step unless it is caught.
    failing = next(line for line in UPDATE.splitlines() if './brievie update "$BAD"' in line)
    assert failing.rstrip().endswith("|| rc=$?"), failing


def test_the_master_key_never_is_an_argument() -> None:
    assert '< "$RUNNER_TEMP/master-key"' in UPDATE
    assert "BRIEVIE_RESTORE_MASTER_KEY" not in UPDATE
    assert 'echo "::add-mask::$key"' in UPDATE


def test_nothing_is_pushed_and_the_stack_goes_at_the_end() -> None:
    code = "\n".join(line for line in UPDATE.splitlines() if not line.lstrip().startswith("#"))
    assert "git push" not in code
    assert code.count("down --volumes") == 1
    assert code.index("down --volumes") > code.index("Take the way back")

"""The CI jobs of the compose-only install, read as text (they run only on a runner)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
SCRIPT = (ROOT / ".github" / "scripts" / "compose-secrets-check.sh").read_text()
JOBS = ("compose-stack", "compose-oldest", "compose-update", "compose-restore", "adopt")


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests read files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def job(name: str) -> str:
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|^  # |\Z)", CI, re.M | re.S)
    assert match, name
    return match.group(0)


def test_the_jobs_exist_and_the_old_ones_are_still_there() -> None:
    for name in (*JOBS, "stack", "update", "restore", "image", "backend"):
        assert re.search(rf"^  {re.escape(name)}:\n", CI, re.M), name


@pytest.mark.parametrize("name", JOBS)
def test_a_compose_job_runs_only_on_code_and_on_a_github_runner(name: str) -> None:
    block = job(name)
    header = block.split("steps:", 1)[0]
    assert "!= 'schedule'" in header
    assert "timeout-minutes:" in header
    assert "permissions:" not in header  # the workflow's contents: read
    first_step = block.split("steps:", 1)[1]
    assert first_step.index('test "$RUNNER_ENVIRONMENT" = github-hosted') < first_step.index(
        "docker"
    )


@pytest.mark.parametrize("name", JOBS)
def test_a_compose_job_always_removes_its_volumes_at_the_end(name: str) -> None:
    block = job(name)
    stop = block.rsplit("- name: Stop", 1)[1]
    assert "if: always()" in stop
    assert "down --volumes" in stop


@pytest.mark.parametrize(
    "name", ["compose-stack", "compose-oldest", "compose-update", "compose-restore"]
)
def test_the_compose_jobs_start_from_the_compose_file_without_deploy_env(name: str) -> None:
    block = job(name)
    assert "-f deploy/compose/docker-compose.yml -f deploy/compose/ci.override.yml" in block
    assert "init-env.sh" not in block
    assert "./brievie" not in block


def test_the_adopt_job_imports_from_a_file_never_from_the_environment() -> None:
    block = job("adopt")
    assert '-v "$PWD/deploy/.env:/import/env:ro"' in block
    assert "python -m apps.installer.init_secrets --import /import/env" in block
    assert "--env-file deploy/.env" in block
    assert "BRIEVIE_MASTER_KEY=" not in block.replace("grep '^BRIEVIE_MASTER_KEY='", "")


def test_no_job_writes_a_literal_secret() -> None:
    for name in JOBS:
        # An action pinned by commit and the checksum of the Compose binary are public.
        block = "\n".join(
            line
            for line in job(name).splitlines()
            if "uses:" not in line and "OLDEST_COMPOSE_SHA256" not in line
        )
        assert not re.search(r"[0-9a-f]{40,}", block), name
        assert not re.search(r"(PASSWORD|SECRET_KEY|MASTER_KEY)=[A-Za-z0-9+/]{12,}", block), name


def test_the_compose_update_job_proves_the_refusals_and_the_freeze() -> None:
    block = job("compose-update")
    # A refusal: init exits 1, nothing is changed, and the old number brings the server back.
    assert block.count('test "$rc" != 0') >= 4
    assert "niet in één keer" in block
    assert "oudere versie" in block
    # The freeze: another session's write fails with the server's own sentence.
    assert "read-only transaction" in block
    assert "pg_db_role_setting" in block


def test_the_oldest_compose_is_installed_by_its_checksum() -> None:
    block = job("compose-oldest")
    assert "OLDEST_COMPOSE: v2.20.2" in block
    assert re.search(r"OLDEST_COMPOSE_SHA256: [0-9a-f]{64}", block)
    assert "sha256sum --check" in block


# --- the secrets check ---


def test_the_secrets_check_has_its_own_selftest_and_never_prints_a_value() -> None:
    assert "selftest" in SCRIPT
    assert 'echo "::add-mask::$value"' in SCRIPT
    assert re.search(r"selftest\) selftest", SCRIPT)
    # Values go into a file of this run and are removed; no `echo "$value"` anywhere.
    assert 'echo "$value"' not in SCRIPT
    assert "trap 'rm -rf" in SCRIPT


def test_the_secrets_check_looks_where_a_secret_could_leak() -> None:
    for place in ("config.txt", "inspect.txt", "processes.txt", "logs-$service.txt"):
        assert place in SCRIPT
    for command in ("$COMPOSE config", "docker inspect", "docker top", "$COMPOSE logs"):
        assert command in SCRIPT
    assert "'{{.Internal}}'" in SCRIPT

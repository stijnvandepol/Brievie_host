"""Text checks on the job `visibility-perf` (.github/workflows/visibility-perf.yml).

The measurement itself runs only on a GitHub runner (plan Task 2, P1). These tests read
the workflow as text: when it runs, with which rights, against which database, and
where its report goes. It lives in its own workflow, so a pull request that does not
touch the visibility code does not start it and the required checks of ci.yml stay as
they are.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PERF = (WORKFLOWS / "visibility-perf.yml").read_text()
CI = (WORKFLOWS / "ci.yml").read_text()

# Everything the measurement imports or runs on: a change here can change its result.
WATCHED = [
    "backend/brievie/settings.py",  # the connection settings (jit=off)
    "backend/apps/workspaces/visibility_sql.py",
    "backend/apps/workspaces/rls.py",
    "backend/apps/workspaces/context.py",
    "backend/apps/documents/services.py",
    "backend/apps/documents/visibility.py",
    "backend/apps/people/services.py",
    "backend/apps/labels/services.py",
    "backend/apps/tasks/services.py",
    "backend/apps/tasks/facts.py",  # Mijn taken's facts, read once per list
    "backend/apps/*/migrations/**",
    "backend/tests/perf/**",
    "backend/uv.lock",
    ".github/workflows/visibility-perf.yml",
]


def code(text: str) -> str:
    """The text without comment lines (a comment may name what the code never does)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def trigger(name: str) -> str:
    on = PERF.split("\npermissions:", 1)[0].split("\non:\n", 1)[1]
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [\w-]+:|\Z)", on, re.M | re.S)
    assert match, name
    return match.group(1)


def paths(block: str) -> list[str]:
    return re.findall(r'^      - "([^"]+)"$', block, re.M)


def job() -> str:
    return PERF.split("\n  visibility-perf:\n", 1)[1]


def test_the_job_runs_on_changes_to_the_visibility_code_on_demand_and_weekly() -> None:
    assert paths(trigger("pull_request")) == WATCHED
    push = trigger("push")
    assert "branches: [main]" in push
    assert paths(push) == WATCHED
    assert "workflow_dispatch:" in PERF.split("\npermissions:", 1)[0]
    crons = re.findall(r'- cron: "(\d+) (\d+) \* \* (\d)"', PERF)
    assert len(crons) == 1
    minute, hour, _ = crons[0]
    assert minute != "0"
    assert int(hour) < 7
    # Not a job of ci.yml: nothing there waits for it or is slowed down by it.
    assert "visibility-perf" not in CI


def test_every_watched_file_exists() -> None:
    for pattern in WATCHED:
        if "*" in pattern or pattern == "backend/apps/documents/visibility.py":
            continue  # a glob, or the module Task 8 creates
        assert (ROOT / pattern).is_file(), pattern


def test_the_job_has_a_timeout_and_default_permissions() -> None:
    header = job().split("steps:", 1)[0]
    assert "timeout-minutes: 30" in header
    assert "runs-on: ubuntu-latest" in header
    assert "\npermissions:\n  contents: read\n" in PERF
    assert "permissions:" not in job()
    assert "continue-on-error" not in code(job())
    for line in PERF.splitlines():
        if "uses:" in line:
            assert re.search(r"uses: [\w./-]+@[0-9a-f]{40} # v\d", line), line


def test_a_newer_push_to_a_pull_request_cancels_the_older_run() -> None:
    group = re.search(r"^  group: (.+)$", PERF, re.M)
    assert group
    assert "github.event_name" in group.group(1)
    assert "github.ref" in group.group(1)
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in PERF


def test_it_sets_brievie_perf_and_runs_only_tests_perf() -> None:
    text = code(job())
    assert (
        'BRIEVIE_PERF=1 BRIEVIE_PERF_REPORT="$RUNNER_TEMP/perf-report.md"'
        " uv run pytest tests/perf -p no:cacheprovider"
    ) in text
    assert text.count("uv run pytest") == 1
    assert "working-directory: backend" in text


def test_it_uses_the_postgres_18_service_and_never_docker_compose() -> None:
    text = code(job())
    images = re.findall(r"image: (postgres:\S+)", text)
    assert images == ["postgres:18"]
    # The same major as the backend job (and so as the image's client, test_deploy_files).
    assert "image: postgres:18\n" in CI
    assert "bash ../deploy/postgres-init/01-roles.sh" in text
    # No literal secret in the file: the role passwords are generated per job and masked.
    assert "openssl rand -hex 24" in text
    assert "::add-mask::" in text
    assert "DATABASE_OWNER_URL=postgres://brievie_owner:$owner@localhost:5432/brievie" in text
    assert "uv sync --frozen" in text
    for forbidden in ("docker compose", "./brievie", "docker run", "make dev"):
        assert forbidden not in text, forbidden


def test_it_publishes_the_report_to_the_step_summary() -> None:
    text = code(job())
    report = text.split("- name: Report", 1)[1].split("- ", 1)[0]
    assert "if: ${{ !cancelled() }}" in report
    assert 'cat "$RUNNER_TEMP/perf-report.md" >> "$GITHUB_STEP_SUMMARY"' in report
    upload = text.split("uses: actions/upload-artifact@", 1)[1]
    assert "if: ${{ !cancelled() }}" in text.split("uses: actions/upload-artifact@", 1)[0][-200:]
    assert "${{ runner.temp }}/perf-report.md" in upload
    assert "${{ runner.temp }}/perf-explain/" in upload

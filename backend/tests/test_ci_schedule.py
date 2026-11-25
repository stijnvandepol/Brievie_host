"""Text checks on the scheduled security audits (ci.yml, codeql.yml, audit-failed.yml).

The schedule itself only a real scheduled run can prove. These tests read the workflows
as text and keep what is easy to break: the days, the jobs a schedule runs, the
concurrency groups, and the issue job's rights and content.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
CI = (WORKFLOWS / "ci.yml").read_text()
CODEQL = (WORKFLOWS / "codeql.yml").read_text()
FAILED = (WORKFLOWS / "audit-failed.yml").read_text()

AUDITED = ["audit-python", "audit-node", "secrets", "workflows", "image", "dast"]
CODE_ONLY = ["backend", "web", "stack", "restore", "update", "ocr-gate"]


def job(text: str, name: str) -> str:
    """The text of one top-level job, up to the next job (or the end of the file)."""
    match = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|\Z)", text, re.M | re.S)
    assert match, name
    return match.group(0)


def code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_both_workflows_run_three_times_a_week_not_on_the_hour() -> None:
    crons = []
    for text in (CI, CODEQL):
        found = re.findall(r'- cron: "(\d+) (\d+) \* \* ([\d,]+)"', text)
        assert len(found) == 1
        minute, hour, days = found[0]
        assert days == "1,3,5"
        assert minute != "0"
        assert int(hour) < 7  # early morning UTC
        crons.append((hour, minute))
        assert "workflow_dispatch:" in text
    assert crons[0] != crons[1]


def test_a_scheduled_run_never_shares_a_concurrency_group_with_a_push_run() -> None:
    for text in (CI, CODEQL):
        group = re.search(r"^  group: (.+)$", text, re.M)
        assert group
        assert "github.event_name" in group.group(1)
        assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in text


def test_the_schedule_skips_the_code_jobs_and_runs_every_audit() -> None:
    for name in CODE_ONLY:
        header = job(CI, name).split("steps:", 1)[0]
        assert "!= 'schedule'" in header, name
        assert "!= 'workflow_dispatch'" in header, name
    for name in AUDITED:
        header = job(CI, name).split("steps:", 1)[0]
        assert "!= 'schedule'" not in header, name
    # Only the two audit-only jobs are limited to the schedule and manual runs.
    for name in ("audit-python", "audit-node"):
        header = job(CI, name).split("steps:", 1)[0]
        assert "github.event_name == 'schedule'" in header, name


def test_the_audit_jobs_run_the_same_commands_as_the_required_checks() -> None:
    # Pull requests are gated by `backend` and `web`; the schedule runs the same audits
    # alone. The two copies must not drift apart.
    pip = re.search(r"(uv export .*\n\s+uvx pip-audit@\S+ .*)\n", job(CI, "backend"))
    assert pip
    assert pip.group(1) in job(CI, "audit-python")
    command = "pnpm audit --prod --audit-level high"
    assert command in job(CI, "web")
    assert command in job(CI, "audit-node")


def test_the_issue_job_runs_only_on_the_schedule_and_asks_for_issues_write_only() -> None:
    for text in (CI, CODEQL):
        block = job(text, "audit-failed")
        header = block.split("uses:", 1)[0]
        assert "github.event_name == 'schedule'" in header
        assert "contains(needs.*.result, 'failure')" in header
        permissions = re.search(r"permissions:\n((?:      .+\n)+)", block)
        assert permissions
        assert permissions.group(1).strip() == "issues: write"
        assert "uses: ./.github/workflows/audit-failed.yml" in block
    needs = re.search(r"needs: \[(.*?)\]", job(CI, "audit-failed"))
    assert needs
    assert [n.strip() for n in needs.group(1).split(",")] == AUDITED
    assert "needs: [analyze]" in job(CODEQL, "audit-failed")


def test_the_issue_workflow_has_no_trigger_but_a_call_and_no_wider_rights() -> None:
    on = FAILED.split("permissions:", 1)[0]
    assert "workflow_call:" in on
    for trigger in ("push:", "pull_request", "schedule:", "workflow_dispatch:"):
        assert trigger not in on
    assert "\npermissions: {}\n" in FAILED
    assert FAILED.count("issues: write") == 1
    for right in ("contents:", "actions:", "pull-requests:", "security-events:"):
        assert right not in code(FAILED)
    assert "actions/checkout" not in FAILED  # it needs no code, and so no contents right


def test_the_issue_holds_the_run_and_the_job_names_and_no_log() -> None:
    script = code(FAILED)
    assert "Scheduled security audit failed" in script
    assert "security-audit" in script
    assert "gh label create" in script
    assert "gh issue comment" in script
    assert "gh issue create" in script
    # No log excerpts, whatever the command.
    for forbidden in ("gh run view", "--log", "gh api", "/logs"):
        assert forbidden not in script, forbidden
    # Context values reach the script through the environment, not the script text.
    assert "${{" not in re.sub(r"^\s+\w+: .*\$\{\{.*\}\}$", "", script, flags=re.M)

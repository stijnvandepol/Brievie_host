"""Text checks on the CI job `restore`: what it must do, and what it must never do.

The job itself runs only on a GitHub runner. These tests read the workflow and its two
scripts as text and compile the seed script; nothing here starts docker, a shell or the
seed (which needs the api container).
"""

import base64
import re
from pathlib import Path

from apps.documents.jpeg import jpeg_size

ROOT = Path(__file__).resolve().parents[2]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
SEED_PATH = ROOT / ".github" / "scripts" / "restore-seed.py"
CHECK_PATH = ROOT / ".github" / "scripts" / "restore-check.sh"
SEED = SEED_PATH.read_text()
CHECK = CHECK_PATH.read_text()

# The job's text, from its own header to the end of the file (it is the last job).
JOB = CI.split("\n  restore:\n", 1)[1]


def code(text: str) -> str:
    """The text without comment lines (a comment may name what the code never does)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def step(name: str) -> str:
    """The text of one step of the job, by (a start of) its name."""
    match = re.search(
        rf"^      - name: {re.escape(name)}.*?\n(.*?)(?=^      - |\Z)",
        JOB,
        re.MULTILINE | re.DOTALL,
    )
    assert match, name
    return match.group(0)


def test_the_job_stands_alone_with_a_timeout_and_the_default_permissions() -> None:
    header = JOB.split("steps:", 1)[0]
    assert "needs:" not in header
    assert "timeout-minutes: 30" in header
    # Like the other jobs: only the workflow's `contents: read`, no wider grant.
    assert "permissions:" not in JOB
    # Like every action in the workflows: pinned to a full commit SHA.
    assert re.search(r"uses: actions/checkout@[0-9a-f]{40} # v\d", JOB)


def test_the_back_up_lives_in_a_folder_that_survives_the_destruction() -> None:
    setup = step("Create deploy/.env")
    assert 'mkdir "$RUNNER_TEMP/backup"' in setup
    assert 'sudo chown 10001:10001 "$RUNNER_TEMP/backup"' in setup
    assert "BRIEVIE_BACKUP_DIR=%s" in setup
    assert "backup-unset" not in JOB  # the named volume goes with `down --volumes`


def test_the_set_up_needs_the_same_disk_flag() -> None:
    assert "./brievie backup --init --allow-same-disk" in step("Set up the back-up")


def test_it_destroys_machine_and_volumes_before_it_restores() -> None:
    destroy = JOB.index("- name: Destroy the machine")
    restore = JOB.index("- name: Restore onto the empty machine")
    assert destroy < restore
    text = step("Destroy the machine")
    assert "down --volumes" in text
    assert "rm deploy/.env" in text
    assert "docker volume ls -q" in text
    # compose needs the secrets of deploy/.env: down comes before the file goes.
    assert text.index("down --volumes") < text.index("rm deploy/.env")
    # The saved key and authority are made before anything is destroyed.
    assert JOB.index("master-key") < destroy
    assert JOB.index("old-root.pem") < destroy


def test_the_restore_reads_the_folder_and_the_key_from_stdin() -> None:
    text = step("Restore onto the empty machine")
    assert 'BRIEVIE_RESTORE_SOURCE="$RUNNER_TEMP/backup"' in text
    assert "BRIEVIE_RESTORE_KEEP_DESTINATION=ja" in text
    assert '--keep-hostname < "$RUNNER_TEMP/master-key"' in text
    assert "Herstel gelukt" in text
    # The key is not a variable of the restore either: it would be in the environment.
    assert "BRIEVIE_RESTORE_MASTER_KEY" not in JOB


def test_the_replace_variant_sets_the_confirmation_and_checks_again() -> None:
    text = step("Restore over the running server")
    assert "BRIEVIE_RESTORE_CONFIRM=WISSEN" in text
    assert "./brievie restore --replace --keep-hostname" in text
    assert "Herstel gelukt" in text
    after = JOB[JOB.index("- name: Restore over the running server") :]
    assert "restore-check.sh" in after
    # Once after the first restore, once after the replace.
    assert JOB.count(".github/scripts/restore-check.sh") == 2


def test_the_restored_server_backs_up_again() -> None:
    text = step("The restored server backs up again")
    assert "./brievie backup" in text
    assert "./brievie status" in text


def test_after_the_restore_every_request_trusts_only_the_old_authority() -> None:
    assert "--insecure" not in code(CHECK)
    assert "--cacert" in CHECK
    assert "$RUNNER_TEMP" not in CHECK.replace("${RUNNER_TEMP:?}", "")
    # Everything from the restore on goes through the check script or ./brievie.
    after = JOB[JOB.index("- name: Restore onto the empty machine") :]
    assert "--insecure" not in code(after)
    assert "curl" not in code(after)
    # The page URL is the real path of getDocumentPage, one request per seeded page.
    assert "/workspaces/$workspace/documents/$document/pages/1" in CHECK
    # The fresh authority is compared with the saved one, byte for byte.
    assert "cmp -s" in CHECK


def test_the_token_never_becomes_a_process_argument() -> None:
    # curl reads the header from a file; the token is masked in the log.
    assert "--header" in CHECK
    assert '"@$work/header"' in CHECK
    assert "Bearer $TOKEN" not in CI
    assert re.search(r"::add-mask::\$\(jq -r \.token", step("Seed two workspaces"))


def test_the_token_and_the_key_are_masked_and_never_echoed() -> None:
    seed_step = step("Seed two workspaces")
    assert "::add-mask::" in seed_step
    assert '> "$RUNNER_TEMP/seed.json"' in seed_step
    key_step = step("Keep the master key")
    assert 'echo "::add-mask::$key"' in key_step
    for text in (seed_step, key_step):
        assert "set -x" not in text
    for line in code(JOB).splitlines():
        # Writing to a file is fine; printing to the log is not.
        if (
            line.strip().startswith(("echo", "printf"))
            and ">" not in line
            and "::add-mask::" not in line
        ):
            assert "$key" not in line, line
            assert "token" not in line.lower(), line


def test_it_never_prints_logs() -> None:
    # The api log holds the installation code.
    assert not re.search(r"\blogs\b", code(JOB))
    assert "docker logs" not in JOB
    assert "ps --all" in step("Container state on failure")
    assert "if: failure()" in step("Container state on failure")


def test_it_always_stops_the_stack_with_its_volumes() -> None:
    text = step("Stop")
    assert "if: always()" in text
    assert "down --volumes" in text


def test_the_seed_is_the_script_the_job_runs() -> None:
    assert SEED_PATH.is_file()
    assert "python manage.py shell" in step("Seed two workspaces")
    assert ".github/scripts/restore-seed.py" in step("Seed two workspaces")


def test_the_scripts_are_executable_where_the_job_runs_them_directly() -> None:
    assert CHECK_PATH.stat().st_mode & 0o111


def test_the_seed_script_compiles_and_prints_one_line() -> None:
    # Only compiled: it needs the api container's settings and database.
    code = compile(SEED, str(SEED_PATH), "exec")
    assert code
    assert SEED.count("sys.stdout.write") == 1
    assert "print(" not in SEED


def test_the_seed_goes_through_the_apps_own_services() -> None:
    for name in (
        "create_workspace",
        "create_document_from_upload",
        "create_pairing_code",
        "pair_device",
        "settings_service.configure",
        "workspace_context",
    ):
        assert name in SEED, name
    # AI stays off: no external call, no consent for an external service.
    assert '"external"' not in SEED


def test_the_seed_page_is_a_valid_jpeg_of_its_own() -> None:
    encoded = "".join(re.findall(r'^\s+"([A-Za-z0-9+/=]+)"$', SEED.split("_BASE_JPEG", 1)[1], re.M))
    data = base64.b64decode(encoded)
    assert jpeg_size(data) == (1, 1)  # what the intake checks
    assert data.endswith(b"\xff\xd9") or data.endswith(b"?\x10")  # the 1x1 file, whole
    assert len(data) == 134


def test_the_job_runs_only_on_a_github_hosted_runner() -> None:
    # Its `down --volumes` targets the fixed project brievie: never on a self-hosted
    # runner that may run Brievie itself.
    first = JOB.split("    steps:\n", 1)[1].split("\n      - ", 2)
    assert "RUNNER_ENVIRONMENT" in first[0] + first[1]
    assert re.search(r'test "\$RUNNER_ENVIRONMENT" = github-hosted', code(JOB))
    assert code(JOB).index("RUNNER_ENVIRONMENT") < code(JOB).index("down --volumes")

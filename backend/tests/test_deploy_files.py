"""Text checks on deploy/: the compose defaults, the image contents and Caddy's answers."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "deploy" / "docker-compose.yml").read_text()
DOCKERFILE = (ROOT / "deploy" / "Dockerfile").read_text()
DOCKERIGNORE = (ROOT / ".dockerignore").read_text().splitlines()
CADDYFILE = (ROOT / "deploy" / "Caddyfile").read_text()
ENV_EXAMPLE = (ROOT / "deploy" / ".env.example").read_text()
PUBLISH_ROOT = (ROOT / "deploy" / "caddy-publish-root.sh").read_text()
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text()


def service(name: str) -> str:
    """The text of one top-level service in the compose file."""
    match = re.search(rf"^  {name}:\n(.*?)(?=^  \S|^\S|\Z)", COMPOSE, re.MULTILINE | re.DOTALL)
    assert match, name
    return match.group(1)


def test_allowed_hosts_and_public_urls_follow_the_hostname() -> None:
    api = service("api")
    assert "ALLOWED_HOSTS: ${ALLOWED_HOSTS:-${BRIEVIE_HOSTNAME" in api
    assert "BRIEVIE_PUBLIC_URLS: ${BRIEVIE_PUBLIC_URLS:-https://${BRIEVIE_HOSTNAME" in api.replace(
        "\n", ""
    )
    # The published HTTPS port is part of the address a phone has to call.
    assert "${BRIEVIE_HTTPS_PORT:-443}" in api


def test_env_example_needs_only_the_hostname() -> None:
    active = {
        line.split("=", 1)[0] for line in ENV_EXAMPLE.splitlines() if re.match(r"^[A-Z_]+=", line)
    }
    assert "BRIEVIE_HOSTNAME" in active
    assert "ALLOWED_HOSTS" not in active
    assert "BRIEVIE_PUBLIC_URLS" not in active


def test_runtime_image_has_no_uv_and_no_tests() -> None:
    runtime = DOCKERFILE.split("AS builder", 1)[1].split("FROM ", 1)[1]
    assert not re.search(r"\buv\b", runtime)
    assert "backend/tests" in DOCKERIGNORE


def test_runtime_image_takes_security_updates_and_drops_pip() -> None:
    # The base image lags behind Debian's security updates between its rebuilds, and its
    # pip (unused at run time) vendors packages with vulnerabilities of their own.
    runtime = DOCKERFILE.split("AS builder", 1)[1].split("FROM ", 1)[1]
    # One RUN instruction: from "RUN " up to the next instruction.
    blocks = re.split(r"\n(?=[A-Z]+ )", runtime)
    apt = next(block for block in blocks if "apt-get install" in block)
    assert apt.startswith("RUN ")
    assert "apt-get upgrade -y" in apt
    assert apt.index("apt-get upgrade") < apt.index("rm -rf /var/lib/apt/lists")
    assert "/usr/local/bin/python3 -m pip uninstall --yes pip" in apt


def test_caddy_compresses_with_zstd_and_gzip() -> None:
    assert re.search(r"^\tencode zstd gzip$", CADDYFILE, re.MULTILINE)


def test_error_responses_carry_the_security_headers() -> None:
    snippet = re.search(r"\(security_headers\) \{(.*?)\n\}", CADDYFILE, re.DOTALL)
    assert snippet
    for name in ("Strict-Transport-Security", "X-Content-Type-Options", "Content-Security-Policy"):
        assert name in snippet.group(1)
    # Defined once, used for normal responses and in every handle_errors block.
    assert len(re.findall(r"import security_headers", CADDYFILE)) == 3
    assert CADDYFILE.count("handle_errors") == 2


def test_caddy_waits_for_a_healthy_api() -> None:
    assert re.search(r"api:\n\s+condition: service_healthy", service("caddy"))
    assert "healthcheck:" in service("api")


def test_api_server_survives_a_slow_upload() -> None:
    # Final review: sync workers with the default 30 s timeout are killed during a slow
    # upload (Caddy streams the body), and two slow uploads would block the whole API.
    dockerfile = (Path(__file__).resolve().parents[2] / "deploy" / "Dockerfile").read_text()
    command = next(line for line in dockerfile.splitlines() if line.startswith("CMD"))
    assert "--worker-class gthread" in command
    assert "--threads 4" in command
    assert "--timeout 120" in command


def test_restic_is_pinned() -> None:
    assert re.search(r"restic[:_/]v?\d+\.\d+\.\d+", DOCKERFILE)
    assert "latest" not in DOCKERFILE


def test_pg_client_major_matches_the_server() -> None:
    # pg_dump refuses a server newer than itself: client and server move together.
    # Every image reference counts; "@postgres:5432" in a URL is a host and port.
    # Only CI's backend job starts a PostgreSQL image; its stack job uses compose.
    image = r"(?<!@)\bpostgres:(\d+)"
    compose = re.findall(image, COMPOSE)
    ci = re.findall(image, CI)
    client = re.findall(r"postgresql-client-(\d+)", DOCKERFILE)
    assert compose
    assert ci
    assert client
    assert len(set(compose + ci + client)) == 1, (compose, ci, client)


def test_every_service_takes_the_backend_image_from_one_variable() -> None:
    # A second stack (another worktree) builds and runs its own image tag.
    for name in ("api", "migrate", "worker", "keeper", "admin"):
        assert "image: ${BRIEVIE_IMAGE:-brievie/backend:local}" in service(name), name
    hard_coded = COMPOSE.replace("${BRIEVIE_IMAGE:-brievie/backend:local}", "")
    assert "brievie/backend:local" not in hard_coded


def test_keeper_has_no_master_key_and_no_superuser_password() -> None:
    keeper = service("keeper")
    for name in (
        "BRIEVIE_MASTER_KEY",
        "POSTGRES_PASSWORD",
        "BRIEVIE_OWNER_PASSWORD",
        "DATABASE_OWNER_URL",
    ):
        assert name not in keeper, name


def test_keeper_serves_the_backup_queue_only() -> None:
    keeper = service("keeper")
    assert '"--queues", "backup"' in keeper
    assert "pipeline" not in keeper


def test_keeper_and_admin_start_without_a_backup_password() -> None:
    # init-env.sh writes none; ./brievie derives it in the container later.
    for name in ("keeper", "admin"):
        assert "BRIEVIE_BACKUP_PASSWORD: ${BRIEVIE_BACKUP_PASSWORD:-}" in service(name), name


def test_keeper_reaches_the_database_as_the_backup_role() -> None:
    assert (
        "@postgres:5432/brievie"
        in service("keeper").split("DATABASE_BACKUP_URL:", 1)[1].split("\n", 1)[0]
    )


def test_keeper_mounts_documents_and_authority_read_only() -> None:
    keeper = service("keeper")
    assert "documents:/data/documents:ro" in keeper
    assert "caddy-authority:/data/authority:ro" in keeper


def test_backup_dir_falls_back_to_an_unset_volume() -> None:
    for name in ("keeper", "admin"):
        text = service(name)
        assert "${BRIEVIE_BACKUP_DIR:-backup-unset}:/backup" in text, name
        assert "BRIEVIE_BACKUP_DIR_SET: ${BRIEVIE_BACKUP_DIR:+true}" in text, name


def test_only_the_admin_backup_mount_takes_its_mode_from_a_variable() -> None:
    # ./brievie exports BRIEVIE_BACKUP_MOUNT_MODE=ro for the restore steps: a restore
    # source is then read-only in the container (restic reads it with --no-lock).
    admin = service("admin")
    assert re.search(
        r"^\s+- \$\{BRIEVIE_BACKUP_DIR:-backup-unset\}:/backup:"
        r"\$\{BRIEVIE_BACKUP_MOUNT_MODE:-rw\}$",
        admin,
        re.MULTILINE,
    )
    keeper = service("keeper")
    assert re.search(r"^\s+- \$\{BRIEVIE_BACKUP_DIR:-backup-unset\}:/backup$", keeper, re.MULTILINE)
    assert "BRIEVIE_BACKUP_MOUNT_MODE" not in keeper
    assert COMPOSE.count("${BRIEVIE_BACKUP_MOUNT_MODE") == 1


def test_admin_is_one_off_behind_a_profile() -> None:
    admin = service("admin")
    assert 'profiles: ["admin"]' in admin
    assert 'restart: "no"' in admin
    assert 'user: "0"' in admin


def test_admin_never_starts_the_image_default_command() -> None:
    # `--profile admin up` would otherwise start gunicorn as root with the master key.
    assert 'command: ["python", "manage.py", "help"]' in service("admin")


def test_admin_mounts_the_documents_volume() -> None:
    # backup --init compares the device of /backup with that of /data/documents; without
    # the volume it would compare against the container's own filesystem.
    assert re.search(r"^\s+- documents:/data/documents$", service("admin"), re.MULTILINE)


def test_migrate_still_runs_before_every_app_container() -> None:
    for name in ("api", "worker", "keeper"):
        assert re.search(
            r"migrate:\n\s+condition: service_completed_successfully", service(name)
        ), name
    assert "depends_on" not in service("admin")


def test_publish_root_copies_the_authority_for_the_keeper() -> None:
    for name in ("root.crt", "root.key", "intermediate.crt", "intermediate.key"):
        assert name in PUBLISH_ROOT, name
    assert "chown 10001:10001" in PUBLISH_ROOT
    assert "chmod 0600" in PUBLISH_ROOT
    assert "chmod 0700 /authority" in PUBLISH_ROOT
    assert re.search(r"^\s+- caddy-authority:/authority$", service("caddy"), re.MULTILINE)


def test_api_never_mounts_the_authority_copy() -> None:
    assert "caddy-authority" not in service("api")
    assert "caddy-authority" not in service("worker")


def test_image_has_the_backup_directories() -> None:
    # restic fails on a missing source path, so the authority copy directory exists even
    # while its volume is empty.
    runtime = DOCKERFILE.split("AS builder", 1)[1].split("FROM ", 1)[1]
    mkdir = next(line for line in runtime.splitlines() if "mkdir -p" in line)
    chown = next(line for line in runtime.splitlines() if "chown brievie:brievie" in line)
    for path in ("/data/documents", "/data/backup-config", "/data/authority", "/backup"):
        own_path = rf"\s{re.escape(path)}(\s|$)"
        assert re.search(own_path, mkdir), path
        assert re.search(own_path, chown), path


def test_make_passes_the_project_to_every_compose_call() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "COMPOSE := docker compose -p $(PROJECT) --project-directory deploy" in makefile
    assert "$(COMPOSE_PROJECT_NAME)" in makefile
    assert "COMPOSE_PROJECT_NAME=//p' deploy/.env" in makefile
    # Every docker compose call goes through $(COMPOSE).
    assert makefile.count("docker compose") == 1


def make_project(tmp_path: Path, env_text: str | None) -> str:
    """The project `make` would pass, from a copy of the Makefile in tmp_path.

    `make -n` only prints the recipe (docker is never started); the env file is the
    copy's own deploy/.env.
    """
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    if env_text is not None:
        (tmp_path / "deploy").mkdir()
        (tmp_path / "deploy" / ".env").write_text(env_text)
    done = subprocess.run(  # noqa: S603
        ["make", "-n", "-s", "-C", str(tmp_path), "down"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )
    match = re.search(r"docker compose -p (\S+) ", done.stdout)
    assert match, done.stdout
    return match.group(1)


@pytest.mark.parametrize(
    ("env_text", "project"),
    [
        ("COMPOSE_PROJECT_NAME=plain\n", "plain"),
        ("export COMPOSE_PROJECT_NAME=exported\n", "exported"),
        ("export  COMPOSE_PROJECT_NAME=spaces\n", "spaces"),
        ("COMPOSE_PROJECT_NAME=first\nexport COMPOSE_PROJECT_NAME=last\n", "last"),
        ("# COMPOSE_PROJECT_NAME=commented\n", "brievie"),
        ("MY_COMPOSE_PROJECT_NAME=other\n", "brievie"),
        (None, "brievie"),
    ],
)
@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_make_reads_the_project_name_in_both_forms(
    tmp_path: Path, env_text: str | None, project: str
) -> None:
    assert make_project(tmp_path, env_text) == project


def test_ensure_backup_role_passes_the_project() -> None:
    script = (ROOT / "deploy" / "ensure-backup-role.sh").read_text()
    assert 'docker compose -p "$project" --project-directory deploy' in script
    assert "${COMPOSE_PROJECT_NAME:-}" in script
    assert 'COMPOSE_PROJECT_NAME=//p\' "$env_file"' in script
    assert script.count("docker compose") == 1
    assert "$compose" not in script


def test_the_image_marks_itself_as_the_container() -> None:
    # The destructive commands refuse to run without it (a Linux host with a real
    # /data/documents would otherwise pass the fixed allow-list).
    runtime = DOCKERFILE.split("AS builder", 1)[1].split("FROM ", 1)[1]
    assert re.search(r"^\s*BRIEVIE_IN_CONTAINER=1\b", runtime, re.MULTILINE), runtime


def test_every_action_in_the_workflows_is_pinned_to_a_commit() -> None:
    # A tag can be moved to other code; a full commit SHA cannot. The version goes in a
    # trailing comment, which Dependabot keeps up to date.
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        for line in workflow.read_text().splitlines():
            match = re.match(r"^\s*(?:- )?uses: (\S+)(.*)$", line)
            if not match or match.group(1).startswith("./"):
                continue
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", match.group(1)), line
            assert re.fullmatch(r" # v\d+(\.\d+)*", match.group(2)), line


def test_ci_runs_the_real_restic_tests_with_the_images_restic() -> None:
    pinned = re.search(r"restic/restic:[0-9.]+@sha256:[0-9a-f]{64}", DOCKERFILE)
    assert pinned
    backend = CI.split("\n  backend:\n", 1)[1].split("\n  web:\n", 1)[0]
    assert pinned.group(0) in backend
    assert backend.index(pinned.group(0)) < backend.index("run: uv run pytest")
    assert "restic version" in backend


def test_the_worker_that_purges_audit_rows_gets_the_retention() -> None:
    # purge_audit_events runs on the pipeline queue, which only the worker serves.
    assert "--queues pipeline" in service("worker")
    line = "BRIEVIE_AUDIT_RETENTION_DAYS: ${BRIEVIE_AUDIT_RETENTION_DAYS:-365}"
    assert line in service("worker")
    assert "# BRIEVIE_AUDIT_RETENTION_DAYS=365" in ENV_EXAMPLE


def test_the_session_limits_reach_every_service_that_reads_them() -> None:
    # The api enforces them (UserSessionMiddleware) and the worker's nightly clean-up
    # (remove_stale_sessions) applies the same limits; with only one of them set, the
    # worker would delete sessions by the defaults.
    for name in ("api", "worker"):
        assert "BRIEVIE_SESSION_IDLE_DAYS: ${BRIEVIE_SESSION_IDLE_DAYS:-14}" in service(name), name
        assert "BRIEVIE_SESSION_MAX_DAYS: ${BRIEVIE_SESSION_MAX_DAYS:-90}" in service(name), name
    assert "# BRIEVIE_SESSION_IDLE_DAYS=14" in ENV_EXAMPLE
    assert "# BRIEVIE_SESSION_MAX_DAYS=90" in ENV_EXAMPLE


def test_the_audit_retention_default_is_the_one_the_settings_use() -> None:
    from django.conf import settings

    assert "BRIEVIE_AUDIT_RETENTION_DAYS:-365}" in service("worker")
    assert settings.BRIEVIE_AUDIT_RETENTION_DAYS == 365


def test_the_worker_that_builds_member_exports_mounts_the_documents() -> None:
    # build_member_export (queue pipeline, so the worker) opens the sealed page files and
    # writes the sealed export under the storage root, which the api reads for the
    # download. Without the volume the worker would use its own container's disk.
    assert re.search(r"^\s+- documents:/data/documents$", service("worker"), re.MULTILINE)

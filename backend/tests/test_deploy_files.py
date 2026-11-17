"""Text checks on deploy/: the compose defaults, the image contents and Caddy's answers."""

import re
from pathlib import Path

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
    assert "s/^COMPOSE_PROJECT_NAME=//p' deploy/.env" in makefile
    # Every docker compose call goes through $(COMPOSE).
    assert makefile.count("docker compose") == 1


def test_ensure_backup_role_passes_the_project() -> None:
    script = (ROOT / "deploy" / "ensure-backup-role.sh").read_text()
    assert 'docker compose -p "$project" --project-directory deploy' in script
    assert "${COMPOSE_PROJECT_NAME:-}" in script
    assert 's/^COMPOSE_PROJECT_NAME=//p\' "$env_file"' in script
    assert script.count("docker compose") == 1
    assert "$compose" not in script

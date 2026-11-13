"""Text checks on deploy/: the compose defaults, the image contents and Caddy's answers."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "deploy" / "docker-compose.yml").read_text()
DOCKERFILE = (ROOT / "deploy" / "Dockerfile").read_text()
DOCKERIGNORE = (ROOT / ".dockerignore").read_text().splitlines()
CADDYFILE = (ROOT / "deploy" / "Caddyfile").read_text()
ENV_EXAMPLE = (ROOT / "deploy" / ".env.example").read_text()


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

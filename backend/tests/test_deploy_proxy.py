"""The proxy image's Dockerfile (text rules; the image itself is built in CI)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROXY = (ROOT / "deploy" / "Dockerfile.proxy").read_text()


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests read files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def test_the_base_is_caddy_2_11_pinned_by_digest() -> None:
    assert re.search(r"^FROM caddy:2\.11@sha256:[0-9a-f]{64}$", PROXY, re.MULTILINE)


def test_the_caddyfile_and_the_script_come_from_deploy() -> None:
    assert "COPY deploy/Caddyfile /etc/caddy/Caddyfile\n" in PROXY
    assert "COPY deploy/caddy-publish-root.sh /etc/caddy/publish-root.sh\n" in PROXY
    assert 'CMD ["/bin/sh", "/etc/caddy/publish-root.sh"]' in PROXY


def test_the_command_matches_the_wrapper_compose() -> None:
    compose = (ROOT / "deploy" / "docker-compose.yml").read_text()
    assert 'command: ["/bin/sh", "/etc/caddy/publish-root.sh"]' in compose
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in compose


def test_the_image_says_its_version_and_has_no_secret() -> None:
    assert "org.opencontainers.image.version" in PROXY
    assert not re.search(r"(PASSWORD|SECRET|TOKEN|KEY)\s*=", PROXY)

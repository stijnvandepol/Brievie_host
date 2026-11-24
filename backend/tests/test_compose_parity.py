"""The compose-only file and the wrapper's compose file may not drift apart.

`drift` is tested on small configs (no Docker, runs anywhere, and proves it can fail);
the test on the real files needs `docker compose config` and runs where CI is set.
"""

import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.compose_support import Config, drift

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests read files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def small_wrapper() -> Config:
    return {
        "services": {
            "postgres": {"environment": {"POSTGRES_PASSWORD": "x", "BRIEVIE_APP_PASSWORD": "x"}},
            "migrate": {
                "environment": {"BRIEVIE_DB_ROLE": "owner", "SECRET_KEY": "x"},
                "depends_on": {"postgres": {"condition": "service_healthy"}},
            },
            "api": {
                "environment": {"SECRET_KEY": "x", "DATABASE_URL": "u", "A": "1"},
                "depends_on": {"migrate": {"condition": "service_completed_successfully"}},
                "healthcheck": {"test": ["CMD", "x"]},
            },
            "caddy": {
                "environment": {"BRIEVIE_HOSTNAME": "h"},
                "ports": [{"published": "443", "target": 443}],
                "depends_on": {"api": {"condition": "service_healthy"}},
            },
        }
    }


def small_compose() -> Config:
    return {
        "services": {
            "init-secrets": {},
            "postgres": {
                "environment": {"POSTGRES_PASSWORD_FILE": "/f"},
                "depends_on": {"init-secrets": {"condition": "service_completed_successfully"}},
            },
            "init": {
                "environment": {
                    "BRIEVIE_DB_ROLE": "owner",
                    "SECRET_KEY_FILE": "/f",
                    "BRIEVIE_COMPOSE_REVISION": "1",
                },
                "depends_on": {"postgres": {"condition": "service_healthy"}},
            },
            "api": {
                "environment": {
                    "SECRET_KEY_FILE": "/f",
                    "DATABASE_URL": "u",
                    "DATABASE_PASSWORD_FILE": "/f",
                    "A": "1",
                },
                "depends_on": {"init": {"condition": "service_completed_successfully"}},
                "healthcheck": {"test": ["CMD", "x"]},
            },
            "caddy": {
                "environment": {"BRIEVIE_HOSTNAME": "h"},
                "ports": [{"published": "443", "target": 443}],
                "depends_on": {"api": {"condition": "service_healthy"}},
            },
        }
    }


def test_equal_definitions_have_no_drift() -> None:
    assert drift(small_wrapper(), small_compose()) == []


def test_a_missing_environment_key_is_drift() -> None:
    compose = copy.deepcopy(small_compose())
    del compose["services"]["api"]["environment"]["A"]  # type: ignore[index]
    assert any("api: environment keys missing" in p for p in drift(small_wrapper(), compose))


def test_an_unknown_extra_key_is_drift() -> None:
    compose = copy.deepcopy(small_compose())
    compose["services"]["api"]["environment"]["NEW_FEATURE"] = "1"  # type: ignore[index]
    assert any("only in the compose file" in p for p in drift(small_wrapper(), compose))


def test_another_port_a_dependency_or_a_healthcheck_is_drift() -> None:
    compose = copy.deepcopy(small_compose())
    services = compose["services"]
    services["caddy"]["ports"] = [{"published": "8443", "target": 443}]  # type: ignore[index]
    services["api"]["depends_on"] = {"postgres": {"condition": "service_started"}}  # type: ignore[index]
    services["api"]["healthcheck"] = {"test": ["CMD", "y"]}  # type: ignore[index]
    found = drift(small_wrapper(), compose)
    assert any("published ports differ" in p for p in found)
    assert any("api: depends_on differs" in p for p in found)
    assert any("healthcheck differs" in p for p in found)


def test_a_missing_service_is_drift() -> None:
    compose = copy.deepcopy(small_compose())
    del compose["services"]["caddy"]  # type: ignore[attr-defined]
    assert any("services differ" in p for p in drift(small_wrapper(), compose))


def test_the_tunnel_service_must_be_the_same_in_both() -> None:
    wrapper, compose = copy.deepcopy(small_wrapper()), copy.deepcopy(small_compose())
    tunnel = {
        "image": "cloudflare/cloudflared:1@sha256:" + "0" * 64,
        "environment": {"TUNNEL_TOKEN": ""},
        "read_only": True,
    }
    wrapper["services"]["cloudflared"] = copy.deepcopy(tunnel)  # type: ignore[index]
    compose["services"]["cloudflared"] = {**copy.deepcopy(tunnel), "networks": ["edge"]}  # type: ignore[index]
    assert drift(wrapper, compose) == []
    compose["services"]["cloudflared"]["read_only"] = False  # type: ignore[index]
    compose["services"]["cloudflared"]["environment"] = {"TUNNEL_TOKEN": "", "X": "1"}  # type: ignore[index]
    found = drift(wrapper, compose)
    assert "cloudflared: read_only differs" in found
    assert "cloudflared: environment differs" in found


def _config(path: str, extra: dict[str, str]) -> Config:
    env = {
        **os.environ,
        # Values only so `docker compose config` interpolates; neither file is started.
        "BRIEVIE_HOSTNAME": "brievie.test",
        "POSTGRES_PASSWORD": "parity-only",
        "BRIEVIE_OWNER_PASSWORD": "parity-only",
        "BRIEVIE_APP_PASSWORD": "parity-only",
        "BRIEVIE_BACKUP_DB_PASSWORD": "parity-only",
        "SECRET_KEY": "parity-only",
        **extra,
    }
    done = subprocess.run(  # noqa: S603
        [
            str(shutil.which("docker")),
            "compose",
            "-f",
            path,
            "--profile",
            "admin",
            "--profile",
            "tunnel",
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    loaded = json.loads(done.stdout)
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.skipif(
    os.environ.get("CI") != "true" or shutil.which("docker") is None,
    reason="runs in CI, where Docker is (never on a developer's machine)",
)
def test_the_real_files_do_not_drift() -> None:
    wrapper = _config(str(ROOT / "deploy" / "docker-compose.yml"), {"BRIEVIE_MASTER_KEY": "parity"})
    compose = _config(str(ROOT / "deploy" / "compose" / "docker-compose.yml"), {})
    assert drift(wrapper, compose) == []


def test_the_caddyfile_in_the_proxy_image_is_the_one_the_wrapper_mounts() -> None:
    dockerfile = (ROOT / "deploy" / "Dockerfile.proxy").read_text()
    wrapper = (ROOT / "deploy" / "docker-compose.yml").read_text()
    assert "COPY deploy/Caddyfile /etc/caddy/Caddyfile" in dockerfile
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in wrapper

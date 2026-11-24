"""Only the standalone compose file makes a server a "compose install" (the web recovery sheet).

Review of PR #45, point 1: the image once carried the variable, so a ./brievie install could
fetch its master key through the web. These tests read every place that could set the flag.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DOCKERFILE = (ROOT / "deploy" / "Dockerfile").read_text()
WRAPPER = (ROOT / "deploy" / "docker-compose.yml").read_text()
STANDALONE = (ROOT / "deploy" / "compose" / "docker-compose.yml").read_text()
DEV = (ROOT / "deploy" / "docker-compose.dev.yml").read_text()


@pytest.fixture(autouse=True)
def _database() -> None:
    """Overrides the suite's database fixture: these tests read files only."""


@pytest.fixture(scope="session", autouse=True)
def _test_role_is_subject_to_rls() -> None:
    """Overrides the suite's session check: no database is used here."""


def test_the_image_and_the_wrapper_files_never_set_the_flag() -> None:
    for name, text in (("Dockerfile", DOCKERFILE), ("wrapper", WRAPPER), ("dev", DEV)):
        assert "BRIEVIE_COMPOSE_INSTALL" not in text, name
        assert "BRIEVIE_COMPOSE_REVISION" not in text, name
    wrapper_script = (ROOT / "brievie").read_text()
    assert "BRIEVIE_COMPOSE_INSTALL" not in wrapper_script


def test_only_the_standalone_file_sets_it_for_every_service_that_needs_it() -> None:
    assert 'BRIEVIE_COMPOSE_INSTALL: "true"' in STANDALONE
    # In the shared settings anchor, so api, worker, keeper, init and admin all have it.
    anchor = STANDALONE.split("x-settings: &settings", 1)[1].split("\nx-backup:", 1)[0]
    assert 'BRIEVIE_COMPOSE_INSTALL: "true"' in anchor
    assert STANDALONE.count("<<: [*settings") + STANDALONE.count("<<: *settings") >= 5


def _setting(environ: dict[str, str]) -> str:
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; print(settings.BRIEVIE_COMPOSE_INSTALL)",
        ],
        cwd=BACKEND,
        env={
            "PATH": os.environ.get("PATH", ""),
            "DJANGO_SETTINGS_MODULE": "brievie.settings",
            "SECRET_KEY": "k" * 60,
            "DATABASE_URL": "postgres://u:p@h:5432/d",
            **environ,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def test_the_flag_is_off_unless_the_variable_says_true() -> None:
    assert _setting({}) == "False"
    # The revision the gate reads is not the flag: it alone must not turn it on.
    assert _setting({"BRIEVIE_COMPOSE_REVISION": "1"}) == "False"
    assert _setting({"BRIEVIE_COMPOSE_INSTALL": "true"}) == "True"

"""deploy/init-env.sh creates a working .env; the shipped example never is one."""

import runpy
import shutil
import stat
import subprocess
from pathlib import Path

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured

import brievie.settings

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def run_script(deploy: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["/bin/sh", str(deploy / "init-env.sh")],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", **env},
    )


@pytest.fixture
def deploy(tmp_path: Path) -> Path:
    target = tmp_path / "deploy"
    target.mkdir()
    for name in ("init-env.sh", ".env.example"):
        shutil.copy(DEPLOY / name, target / name)
    return target


def test_example_as_shipped_is_rejected_by_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in read_env(DEPLOY / ".env.example").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)

    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        runpy.run_path(str(Path(brievie.settings.__file__)))


def test_script_creates_a_private_env_without_placeholders(deploy: Path) -> None:
    result = run_script(deploy)

    env_file = deploy / ".env"
    values = read_env(env_file)
    assert not [k for k, v in values.items() if "change-me" in v]
    assert len(values["SECRET_KEY"]) >= 50
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    # The URLs carry the generated passwords; all three passwords differ.
    assert values["DATABASE_URL"].startswith(
        f"postgres://brievie_app:{values['BRIEVIE_APP_PASSWORD']}@"
    )
    assert values["DATABASE_OWNER_URL"].startswith(
        f"postgres://brievie_owner:{values['BRIEVIE_OWNER_PASSWORD']}@"
    )
    passwords = {
        values[k] for k in ("POSTGRES_PASSWORD", "BRIEVIE_OWNER_PASSWORD", "BRIEVIE_APP_PASSWORD")
    }
    assert len(passwords) == 3
    # No secret is printed.
    assert values["SECRET_KEY"] not in result.stdout + result.stderr
    assert values["BRIEVIE_APP_PASSWORD"] not in result.stdout + result.stderr


def test_second_run_leaves_the_file_byte_identical(deploy: Path) -> None:
    run_script(deploy)
    before = (deploy / ".env").read_bytes()

    run_script(deploy)

    assert (deploy / ".env").read_bytes() == before


def test_existing_env_is_never_modified(deploy: Path) -> None:
    (deploy / ".env").write_text("SECRET_KEY=mine\n")

    run_script(deploy)

    assert (deploy / ".env").read_text() == "SECRET_KEY=mine\n"


def test_postgres_port_can_be_chosen(deploy: Path) -> None:
    run_script(deploy, POSTGRES_PORT="55432")

    values = read_env(deploy / ".env")
    assert values["POSTGRES_PORT"] == "55432"
    assert ":55432/" in values["DATABASE_URL"]
    assert ":55432/" in values["DATABASE_OWNER_URL"]


def test_generated_env_is_accepted_by_the_settings(
    deploy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_script(deploy)
    for key, value in read_env(deploy / ".env").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(environ.Env, "read_env", lambda *args, **kwargs: None)

    values = runpy.run_path(str(Path(brievie.settings.__file__)))

    assert values["DEBUG"] is False

"""deploy/init-env.sh creates a working .env; the shipped example never is one."""

import runpy
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import environ
import pytest
from django.core.exceptions import ImproperlyConfigured

import brievie.settings
from apps.common.crypto import derive_backup_password

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
MASTER = "5a" * 32
BACKUP_KEYS = ["BRIEVIE_BACKUP_DB_PASSWORD", "DATABASE_BACKUP_URL", "BRIEVIE_BACKUP_PASSWORD"]


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def run_script(deploy: Path, *, check: bool = True, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["/bin/sh", str(deploy / "init-env.sh")],
        capture_output=True,
        text=True,
        check=check,
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


def test_fresh_env_has_a_32_byte_master_key(deploy: Path) -> None:
    result = run_script(deploy)

    key = read_env(deploy / ".env")["BRIEVIE_MASTER_KEY"]
    assert len(bytes.fromhex(key)) == 32
    assert key not in result.stdout + result.stderr


def test_existing_env_without_a_master_key_gets_it_and_the_backup_lines(deploy: Path) -> None:
    (deploy / ".env").write_text("SECRET_KEY=mine\nDEBUG=false")

    result = run_script(deploy)

    lines = (deploy / ".env").read_text().splitlines()
    assert lines[:2] == ["SECRET_KEY=mine", "DEBUG=false"]
    assert [line.split("=", 1)[0] for line in lines[2:]] == [
        "BRIEVIE_MASTER_KEY",
        *BACKUP_KEYS,
    ]
    values = read_env(deploy / ".env")
    key = values["BRIEVIE_MASTER_KEY"]
    assert len(bytes.fromhex(key)) == 32
    assert values["BRIEVIE_BACKUP_PASSWORD"] == derive_backup_password(bytes.fromhex(key))
    assert key not in result.stdout + result.stderr


def test_existing_env_gets_exactly_the_missing_backup_lines(deploy: Path) -> None:
    old = f"SECRET_KEY=mine\nPOSTGRES_PORT=5433\nBRIEVIE_MASTER_KEY={MASTER}\n# a comment\n"
    (deploy / ".env").write_text(old)

    run_script(deploy)

    text = (deploy / ".env").read_text()
    assert text.startswith(old)
    added = text.removeprefix(old).splitlines()
    assert [line.split("=", 1)[0] for line in added] == BACKUP_KEYS
    values = read_env(deploy / ".env")
    db_password = values["BRIEVIE_BACKUP_DB_PASSWORD"]
    assert len(bytes.fromhex(db_password)) == 24
    assert values["DATABASE_BACKUP_URL"] == (
        f"postgres://brievie_backup:{db_password}@localhost:5433/brievie"
    )
    assert values["BRIEVIE_BACKUP_PASSWORD"] == derive_backup_password(bytes.fromhex(MASTER))


def test_existing_backup_db_password_is_reused_in_a_missing_url(deploy: Path) -> None:
    (deploy / ".env").write_text(f"BRIEVIE_MASTER_KEY={MASTER}\nBRIEVIE_BACKUP_DB_PASSWORD=mine\n")

    run_script(deploy)

    values = read_env(deploy / ".env")
    assert values["BRIEVIE_BACKUP_DB_PASSWORD"] == "mine"
    assert values["DATABASE_BACKUP_URL"] == "postgres://brievie_backup:mine@localhost:5432/brievie"


def test_existing_env_with_all_lines_is_never_modified(deploy: Path) -> None:
    text = (
        "SECRET_KEY=mine\nBRIEVIE_MASTER_KEY=abc\nBRIEVIE_BACKUP_DB_PASSWORD=x\n"
        "DATABASE_BACKUP_URL=postgres://brievie_backup:x@localhost:5432/brievie\n"
        "BRIEVIE_BACKUP_PASSWORD=set-by-the-organisation\n"
    )
    (deploy / ".env").write_text(text)

    run_script(deploy)

    assert (deploy / ".env").read_text() == text


def test_malformed_master_key_is_not_used_for_the_backup_password(deploy: Path) -> None:
    (deploy / ".env").write_text("BRIEVIE_MASTER_KEY=change-me-master-key\n")

    result = run_script(deploy, check=False)

    assert result.returncode == 1
    assert "BRIEVIE_MASTER_KEY" in result.stderr
    assert "BRIEVIE_BACKUP_PASSWORD" not in (deploy / ".env").read_text()


def test_fresh_env_has_backup_lines(deploy: Path) -> None:
    run_script(deploy, POSTGRES_PORT="5433")

    values = read_env(deploy / ".env")
    db_password = values["BRIEVIE_BACKUP_DB_PASSWORD"]
    assert len(bytes.fromhex(db_password)) == 24
    assert values["DATABASE_BACKUP_URL"] == (
        f"postgres://brievie_backup:{db_password}@localhost:5433/brievie"
    )
    assert db_password not in {values["BRIEVIE_OWNER_PASSWORD"], values["BRIEVIE_APP_PASSWORD"]}
    assert len(values["BRIEVIE_BACKUP_PASSWORD"]) == 64


def test_shell_derivation_equals_python(deploy: Path) -> None:
    run_script(deploy)

    values = read_env(deploy / ".env")
    master = bytes.fromhex(values["BRIEVIE_MASTER_KEY"])
    assert values["BRIEVIE_BACKUP_PASSWORD"] == derive_backup_password(master)


def test_python_fallback_derives_the_same_password(deploy: Path, tmp_path: Path) -> None:
    # A PATH with python3 but without openssl.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("cat", "chmod", "cut", "dirname", "grep", "mv", "rm", "sed", "tail", "tr"):
        found = shutil.which(tool)
        assert found is not None
        (bin_dir / tool).symlink_to(found)
    (bin_dir / "python3").symlink_to(sys.executable)
    (deploy / ".env").write_text(f"BRIEVIE_MASTER_KEY={MASTER}\n")

    run_script(deploy, PATH=str(bin_dir))

    values = read_env(deploy / ".env")
    assert values["BRIEVIE_BACKUP_PASSWORD"] == derive_backup_password(bytes.fromhex(MASTER))
    assert len(bytes.fromhex(values["BRIEVIE_BACKUP_DB_PASSWORD"])) == 24


def test_init_env_never_prints_a_secret(deploy: Path) -> None:
    fresh = run_script(deploy)
    values = read_env(deploy / ".env")
    (deploy / ".env").write_text(f"BRIEVIE_MASTER_KEY={MASTER}\n")
    existing = run_script(deploy)
    values_existing = read_env(deploy / ".env")

    for result, secrets in ((fresh, values), (existing, values_existing)):
        printed = result.stdout + result.stderr
        for key in ("BRIEVIE_MASTER_KEY", *BACKUP_KEYS):
            assert secrets[key] not in printed


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

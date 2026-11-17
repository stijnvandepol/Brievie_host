import os
import stat
from pathlib import Path

import pytest
from pytest_django import Settings

from apps.backup.envcopy import ENV_COPY_NAME, EXCLUDED_KEYS, store_env_copy
from apps.backup.errors import BackupError

SECRET = "s3cr3t-value-that-must-not-leak"


@pytest.fixture
def config_dir(settings: Settings, tmp_path: Path) -> Path:
    directory = tmp_path / "backup-config"
    settings.BRIEVIE_BACKUP_CONFIG_DIR = str(directory)
    return directory


@pytest.mark.parametrize("key", EXCLUDED_KEYS)
@pytest.mark.parametrize("prefix", ["", "  ", "\t", "export ", "  export  "])
def test_env_copy_refuses_every_secret(key: str, prefix: str, config_dir: Path) -> None:
    text = f"BRIEVIE_HOSTNAME=brievie.local\n{prefix}{key}={SECRET}\nOTHER=1\n"
    with pytest.raises(BackupError) as caught:
        store_env_copy(text)
    assert caught.value.code == "env_copy_has_secret"
    assert SECRET not in str(caught.value)
    assert key not in str(caught.value)
    assert not (config_dir / ENV_COPY_NAME).exists()
    assert not config_dir.exists() or list(config_dir.iterdir()) == []


def test_env_copy_keeps_an_existing_copy_when_refused(config_dir: Path) -> None:
    store_env_copy("A=1\n")
    with pytest.raises(BackupError):
        store_env_copy(f"BRIEVIE_MASTER_KEY={SECRET}\n")
    assert (config_dir / ENV_COPY_NAME).read_text() == "A=1\n"


def test_env_copy_allows_a_key_that_only_starts_the_same(config_dir: Path) -> None:
    text = "BRIEVIE_MASTER_KEY_FILE=/run/key\n# BRIEVIE_MASTER_KEY=x\nPOSTGRES_PASSWORD_HINT=a\n"
    store_env_copy(text)
    assert (config_dir / ENV_COPY_NAME).read_text() == text


def test_env_copy_is_private(config_dir: Path) -> None:
    text = "BRIEVIE_HOSTNAME=brievie.local\nPOSTGRES_PORT=5433\n"
    store_env_copy(text)
    path = config_dir / ENV_COPY_NAME
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert path.read_text() == text
    assert sorted(p.name for p in config_dir.iterdir()) == [ENV_COPY_NAME]


def test_env_copy_replaces_the_previous_copy(config_dir: Path) -> None:
    store_env_copy("A=1\n")
    store_env_copy("A=2\n")
    assert (config_dir / ENV_COPY_NAME).read_text() == "A=2\n"


def test_command_stores_stdin_and_prints_nothing(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    from django.core.management import call_command

    monkeypatch.setattr("sys.stdin", io.StringIO("A=1\n"))
    call_command("store_env_copy")
    assert capsys.readouterr().out == ""
    assert (config_dir / ENV_COPY_NAME).read_text() == "A=1\n"


def test_command_refuses_a_secret_without_echoing_it(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    from django.core.management import CommandError, call_command

    monkeypatch.setattr("sys.stdin", io.StringIO(f"BRIEVIE_MASTER_KEY={SECRET}\n"))
    with pytest.raises(CommandError) as caught:
        call_command("store_env_copy")
    assert SECRET not in str(caught.value)

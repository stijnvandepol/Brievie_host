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
    text = (
        "BRIEVIE_MASTER_KEY_FILE=/run/key\n# BRIEVIE_MASTER_KEY=\n"
        "#POSTGRES_PASSWORD = \nPOSTGRES_PASSWORD_HINT=a\n"
    )
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

    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"A=1\n"), encoding="utf-8"))
    call_command("store_env_copy")
    assert capsys.readouterr().out == ""
    assert (config_dir / ENV_COPY_NAME).read_text() == "A=1\n"


def test_command_refuses_a_secret_without_echoing_it(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    from django.core.management import CommandError, call_command

    monkeypatch.setattr(
        "sys.stdin",
        io.TextIOWrapper(io.BytesIO(f"BRIEVIE_MASTER_KEY={SECRET}\n".encode())),
    )
    with pytest.raises(CommandError) as caught:
        call_command("store_env_copy")
    assert SECRET not in str(caught.value)


SPELLINGS = [
    "{key}: {v}",
    "{key} : {v}",
    "export {key}: {v}",
    "\ufeff{key}={v}",
    "\ufeffexport {key}={v}",
    "'{key}'={v}",
    '"{key}"={v}',
    "export '{key}' = {v}",
    '  export "{key}": {v}',
    "{lower}={v}",
    "{mixed}={v}",
    "export {mixed} = {v}",
    "#{key}={v}",
    "# {key} = {v}",
    "  #  export {key}: {v}",
    "# '{key}'={v}",
]


@pytest.mark.parametrize("key", EXCLUDED_KEYS)
@pytest.mark.parametrize("spelling", SPELLINGS)
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_env_copy_refuses_every_spelling(
    key: str, spelling: str, newline: str, config_dir: Path
) -> None:
    store_env_copy("A=1\n")
    line = spelling.format(key=key, lower=key.lower(), mixed=key.title(), v=SECRET)
    text = newline.join(["BEFORE=1", line, "AFTER=2", ""])
    with pytest.raises(BackupError) as caught:
        store_env_copy(text)
    assert caught.value.code == "env_copy_has_secret"
    assert SECRET not in str(caught.value)
    assert SECRET not in repr(caught.value)
    assert key.lower() not in str(caught.value).lower()
    assert (config_dir / ENV_COPY_NAME).read_text() == "A=1\n"
    assert [p.name for p in config_dir.iterdir()] == [ENV_COPY_NAME]


def test_env_copy_refuses_a_bom_at_the_start_of_the_text(config_dir: Path) -> None:
    with pytest.raises(BackupError):
        store_env_copy(f"\ufeffBRIEVIE_MASTER_KEY={SECRET}\n")
    assert not config_dir.exists()


@pytest.mark.parametrize("key", EXCLUDED_KEYS)
@pytest.mark.parametrize("empty", ["#{key}=", "# {key} =", "# {key}=''", '# {key}=""', "# {key}:"])
def test_env_copy_allows_a_commented_key_without_a_value(
    key: str, empty: str, config_dir: Path
) -> None:
    text = empty.format(key=key) + "\r\nA=1\r\n"
    store_env_copy(text)
    assert (config_dir / ENV_COPY_NAME).read_bytes() == text.encode()


def test_env_copy_is_utf8_and_the_folder_private(config_dir: Path) -> None:
    store_env_copy("NAAM=caf\u00e9\n")
    assert (config_dir / ENV_COPY_NAME).read_bytes() == "NAAM=caf\u00e9\n".encode()
    assert stat.S_IMODE(os.stat(config_dir).st_mode) == 0o700


def test_env_copy_syncs_before_the_replace(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        calls.append("fsync")
        real_fsync(fd)

    def replace(source: str, target: Path) -> None:
        calls.append("replace")
        real_replace(source, target)

    monkeypatch.setattr("os.fsync", fsync)
    monkeypatch.setattr("os.replace", replace)
    store_env_copy("A=1\n")
    assert calls == ["fsync", "replace"]


def test_command_reads_stdin_as_utf8(config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from django.core.management import call_command

    monkeypatch.setattr(
        "sys.stdin", io.TextIOWrapper(io.BytesIO("NAAM=caf\u00e9\n".encode()), encoding="ascii")
    )
    call_command("store_env_copy")
    assert (config_dir / ENV_COPY_NAME).read_text(encoding="utf-8") == "NAAM=caf\u00e9\n"

from pathlib import Path
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.backup import destination, services
from tests.backup_support import FakeBinary

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def ready(settings: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    mount = tmp_path / "backup"
    mount.mkdir()
    settings.BRIEVIE_BACKUP_DIR_SET = True  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_MOUNT = str(mount)  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_PASSWORD = "p" * 40  # type: ignore[attr-defined]
    monkeypatch.setattr(destination, "_device", lambda path: 2 if str(path) == str(mount) else 1)
    return mount


def test_initialise_destination_returns_the_kind(ready: Path, fake_restic: FakeBinary) -> None:
    assert services.initialise_destination(allow_same_disk=False) == "folder"
    assert fake_restic.calls == [["init"]]


def test_services_chowns_only_as_root(ready: Path, fake_restic: FakeBinary) -> None:
    with (
        mock.patch("os.geteuid", return_value=0),
        mock.patch("os.fchown") as fchown,
    ):
        services.initialise_destination(allow_same_disk=False)
    assert fchown.call_args_list
    assert all(call.args[1:] == (10001, 10001) for call in fchown.call_args_list)


def test_command_prints_one_sentence(
    ready: Path, fake_restic: FakeBinary, capsys: pytest.CaptureFixture[str]
) -> None:
    call_command("backup_init")
    out = capsys.readouterr().out
    assert out.strip() == "De back-up is ingesteld."
    assert len(out.strip().splitlines()) == 1


def test_command_fails_with_the_dutch_message(settings: object) -> None:
    with pytest.raises(CommandError) as caught:
        call_command("backup_init")
    assert str(caught.value) == "Er is nog geen back-up ingesteld."


def test_command_reports_an_unwritable_destination_in_one_sentence(
    ready: Path, fake_restic: FakeBinary
) -> None:
    with (
        mock.patch("os.fchown", side_effect=PermissionError),
        mock.patch("os.geteuid", return_value=0),
        pytest.raises(CommandError) as caught,
    ):
        call_command("backup_init")
    assert "SELFHOST.md" in str(caught.value)


def test_command_names_its_code_for_the_wrapper(
    settings: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(CommandError):
        call_command("backup_init")
    assert "code=" not in capsys.readouterr().err

    monkeypatch.setenv("BRIEVIE_WRAPPER", "1")
    with pytest.raises(CommandError) as caught:
        call_command("backup_init")
    assert capsys.readouterr().err.strip() == "code=not_configured"
    assert str(caught.value) == "Er is nog geen back-up ingesteld."

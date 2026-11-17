from datetime import date

import pytest
from django.core.management import call_command

from apps.backup import sheet
from apps.common import crypto
from apps.devices import pki

KEY_HEX = "5a" * 32
GROUPED = " ".join(["5a5a"] * 16)


@pytest.fixture(autouse=True)
def configured(settings: object) -> None:
    settings.BRIEVIE_MASTER_KEY = KEY_HEX  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_PASSWORD = ""  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_REPOSITORY = ""  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_DIR_SET = False  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_DIR_HOST = ""  # type: ignore[attr-defined]
    settings.BRIEVIE_HOSTNAME = "brievie.thuis.example"  # type: ignore[attr-defined]


def test_key_is_grouped_in_fours() -> None:
    assert sheet.group_key("5a" * 32) == GROUPED
    assert sheet.group_key("AB" * 32) == " ".join(["abab"] * 16)


def test_sheet_holds_name_date_key_fingerprint_destination(
    settings: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.BRIEVIE_HOSTNAME = "<b>x"  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_DIR_SET = True  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_DIR_HOST = "/Volumes/Back & Up"  # type: ignore[attr-defined]
    monkeypatch.setattr(pki, "ca_certificate_sha256", lambda: "ab" * 32)

    data = sheet.sheet_data()
    html = sheet.render_sheet(data)

    assert "Herstelblad Brievie" in html
    assert "&lt;b&gt;x" in html
    assert "<b>x" not in html
    assert GROUPED in html
    assert "ab" * 32 in html
    assert "/Volumes/Back &amp; Up" in html
    assert data.created_on == date.today()
    assert html.count("./brievie restore") >= 1
    assert "Herstel gelukt" in html


def test_destination_is_not_yet_set_when_missing_or_broken(settings: object) -> None:
    assert sheet.sheet_data().destination == "Nog niet ingesteld"
    settings.BRIEVIE_BACKUP_DIR_SET = True  # type: ignore[attr-defined]
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://x.example/r"  # type: ignore[attr-defined]
    assert sheet.sheet_data().destination == "Nog niet ingesteld"


def test_destination_shows_the_repository_for_a_remote(settings: object) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://x.example/r"  # type: ignore[attr-defined]
    assert sheet.sheet_data().destination == "rest:https://x.example/r"


def test_sheet_has_a_second_line_only_with_a_separate_password(settings: object) -> None:
    assert sheet.sheet_data().separate_password is None
    assert "Apart wachtwoord" not in sheet.render_sheet(sheet.sheet_data())

    settings.BRIEVIE_BACKUP_PASSWORD = crypto.derive_backup_password(  # type: ignore[attr-defined]
        crypto.master_key()
    )
    assert sheet.sheet_data().separate_password is None

    settings.BRIEVIE_BACKUP_PASSWORD = "eigen-wachtwoord-<1>"  # type: ignore[attr-defined]
    data = sheet.sheet_data()
    assert data.separate_password == "eigen-wachtwoord-<1>"
    html = sheet.render_sheet(data)
    assert "Apart wachtwoord" in html
    assert "eigen-wachtwoord-&lt;1&gt;" in html


def test_sheet_is_self_contained() -> None:
    html = sheet.render_sheet(sheet.sheet_data())
    assert "<script" not in html
    assert 'src="http' not in html
    assert 'href="http' not in html
    assert "http://" not in html
    assert "https://" not in html
    assert "<img" not in html
    assert "<style>" in html


def test_sheet_command_logs_nothing(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    with caplog.at_level(0):
        call_command("recovery_sheet")
    out = capsys.readouterr()
    assert caplog.text == ""
    assert GROUPED in out.out
    assert KEY_HEX not in out.err
    assert GROUPED not in out.err

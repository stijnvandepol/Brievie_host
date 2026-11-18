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


@pytest.mark.parametrize(
    "value",
    ["change-me-later", "  ", "", "  change-me  ", "5A" * 32],
)
def test_no_second_line_for_placeholder_blank_or_derived_value(
    settings: object, value: str
) -> None:
    if value == "5A" * 32:
        value = crypto.derive_backup_password(crypto.master_key()).upper()
    settings.BRIEVIE_BACKUP_PASSWORD = value  # type: ignore[attr-defined]
    data = sheet.sheet_data()
    assert data.separate_password is None
    assert "Apart wachtwoord" not in sheet.render_sheet(data)


def test_separate_password_is_stripped(settings: object) -> None:
    settings.BRIEVIE_BACKUP_PASSWORD = "  eigen  "  # type: ignore[attr-defined]
    assert sheet.sheet_data().separate_password == "eigen"


def test_hex_separate_password_is_grouped_and_wraps(settings: object) -> None:
    settings.BRIEVIE_BACKUP_PASSWORD = "ab12" * 16  # type: ignore[attr-defined]
    html = sheet.render_sheet(sheet.sheet_data())
    assert " ".join(["ab12"] * 16) in html
    assert 'class="key password"' in html
    assert "overflow-wrap: anywhere" in html


def test_non_hex_separate_password_is_shown_as_is(settings: object) -> None:
    settings.BRIEVIE_BACKUP_PASSWORD = "een-lang-wachtwoord-2026"  # type: ignore[attr-defined]
    html = sheet.render_sheet(sheet.sheet_data())
    assert 'class="key password"' in html
    assert "een-lang-wachtwoord-2026" in html


def test_repr_hides_key_and_password(settings: object) -> None:
    settings.BRIEVIE_BACKUP_PASSWORD = "eigen-wachtwoord-1"  # type: ignore[attr-defined]
    text = repr(sheet.sheet_data())
    assert "5a5a" not in text
    assert "eigen-wachtwoord-1" not in text


def test_repository_with_credentials_is_not_shown(settings: object) -> None:
    settings.BRIEVIE_BACKUP_REPOSITORY = "rest:https://u:p@h/r"  # type: ignore[attr-defined]
    html = sheet.render_sheet(sheet.sheet_data())
    assert "Nog niet ingesteld" in html
    assert "u:p" not in html


def test_text_names_both_secrets_only_with_a_separate_password(settings: object) -> None:
    plain = sheet.render_sheet(sheet.sheet_data())
    assert "Zonder deze sleutel kan niemand de back-up openen." in plain
    assert "het aparte wachtwoord" not in plain
    assert "./brievie restore" in plain
    assert "Herstel gelukt." in plain

    settings.BRIEVIE_BACKUP_PASSWORD = "eigen-wachtwoord-1"  # type: ignore[attr-defined]
    both = sheet.render_sheet(sheet.sheet_data())
    assert (
        "Zonder deze sleutel en het aparte wachtwoord kan niemand de back-up openen. "
        "De makers van Brievie ook niet."
    ) in both
    assert "vraagt dan om twee dingen" in both
    assert "./brievie restore" in both
    assert "Herstel gelukt." in both


def test_date_is_dutch_even_when_another_language_is_active() -> None:
    from django.utils import translation

    data = sheet.SheetData(
        hostname="h",
        created_on=date(2026, 3, 5),
        master_key_groups=GROUPED,
        separate_password=None,
        authority_sha256=None,
        destination="x",
        restore_steps=[],
    )
    with translation.override("en"):
        html = sheet.render_sheet(data)
    assert "5 maart 2026" in html


def test_the_last_step_quotes_the_final_line() -> None:
    plain = sheet.render_sheet(sheet.sheet_data())
    assert "Wacht tot je „Herstel gelukt.” ziet." in plain or (
        "Wacht tot je &bdquo;Herstel gelukt.&rdquo; ziet." in plain
    )
    assert "Open Brievie daarna zoals je gewend bent." in plain
    assert "je Herstel gelukt. ziet" not in plain

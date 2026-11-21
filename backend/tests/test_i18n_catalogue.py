"""The English catalogue: complete, up to date, and faithful to the Dutch texts.

A new Dutch text in a gettext call fails here until it has its English translation:
run `make messages`, translate the empty msgstr in backend/locale/en/LC_MESSAGES/django.po
and run `make messages` again (it writes the .mo).
"""

import gettext
import io
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import translation

from apps.common import catalogue
from apps.common.management.commands.update_messages import catalogue_paths, render

BASE_DIR = Path(settings.BASE_DIR)


def test_every_dutch_text_has_an_english_translation() -> None:
    messages, _po, _mo = render("en")

    missing = [m.msgid for m in messages if not m.translated]

    assert len(messages) > 400
    assert missing == []


def test_the_committed_catalogue_is_up_to_date() -> None:
    po_path, mo_path = catalogue_paths("en")
    _messages, po, mo = render("en")

    assert po_path.read_text("utf-8") == po, "run `make messages` and commit the result"
    assert mo_path.read_bytes() == mo, "run `make messages` and commit the result"


def test_the_check_command_passes() -> None:
    out = io.StringIO()
    call_command("update_messages", "--check", stdout=out)
    assert "up to date" in out.getvalue()


def test_a_translation_keeps_the_placeholders_of_its_dutch_text() -> None:
    messages, _po, _mo = render("en")

    wrong = [
        m.msgid
        for m in messages
        if any(catalogue.placeholders(t) != catalogue.placeholders(m.msgid) for t in m.translations)
    ]

    assert wrong == []


def test_every_gettext_call_names_its_text_literally() -> None:
    # An f-string or a variable can never be in the catalogue.
    assert catalogue.non_literal_calls(BASE_DIR) == []


def test_django_answers_in_english_from_the_catalogue() -> None:
    with translation.override("en"):
        assert translation.gettext("Je bent niet ingelogd.") == "You are not signed in."
    with translation.override("nl"):
        assert translation.gettext("Je bent niet ingelogd.") == "Je bent niet ingelogd."


def test_extract_reads_calls_aliases_plurals_and_templates(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "a.py").write_text(
        "from django.utils.translation import gettext as _, gettext_noop, ngettext\n"
        "x = _('Eén')\n"
        "y = gettext_noop('Twee')\n"
        "z = ngettext('%(n)s brief', '%(n)s brieven', 2)\n"
        "w = _('Eén')\n",
        encoding="utf-8",
    )
    (tmp_path / "apps" / "migrations").mkdir()
    (tmp_path / "apps" / "migrations" / "m.py").write_text(
        "from django.utils.translation import gettext as _\nx = _('Niet')\n", encoding="utf-8"
    )
    (tmp_path / "apps" / "t.html").write_text(
        '{% load i18n %}<p>{% translate "Drie" %}</p>', encoding="utf-8"
    )

    found = [(m.msgid, m.plural) for m in catalogue.extract(tmp_path, ("apps",))]

    assert found == [
        ("Eén", ""),
        ("Twee", ""),
        ("%(n)s brief", "%(n)s brieven"),
        ("Drie", ""),
    ]


def test_non_literal_calls_are_found(tmp_path: Path) -> None:
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "a.py").write_text(
        "from django.utils.translation import gettext as _\nname = 'x'\nx = _(f'Hoi {name}')\n",
        encoding="utf-8",
    )

    assert catalogue.non_literal_calls(tmp_path, ("apps",)) == ["apps/a.py:3"]


def test_po_and_mo_round_trip() -> None:
    messages = [
        catalogue.Message('Zeg "hoi"\nen tab\t', "", ['Say "hi"\nand tab\t'], ["apps/a.py"]),
        catalogue.Message("%(n)s brief", "%(n)s brieven", ["%(n)s letter", "%(n)s letters"]),
        catalogue.Message("Nog niet", "", [""]),
    ]
    po = catalogue.write_po(messages, language="en")

    assert catalogue.read_po(po) == messages

    mo = gettext.GNUTranslations(io.BytesIO(catalogue.write_mo(messages, language="en")))
    assert mo.gettext('Zeg "hoi"\nen tab\t') == 'Say "hi"\nand tab\t'
    assert mo.ngettext("%(n)s brief", "%(n)s brieven", 1) == "%(n)s letter"
    assert mo.ngettext("%(n)s brief", "%(n)s brieven", 3) == "%(n)s letters"
    # Untranslated: gettext falls back to the Dutch text.
    assert mo.gettext("Nog niet") == "Nog niet"


def test_merge_keeps_known_translations_and_drops_unused_ones() -> None:
    source = [catalogue.Message("Een", files=["a.py"]), catalogue.Message("Twee")]
    existing = [catalogue.Message("Een", "", ["One"]), catalogue.Message("Weg", "", ["Gone"])]

    merged = catalogue.merge(source, existing)

    assert [(m.msgid, m.translations) for m in merged] == [("Een", ["One"]), ("Twee", [])]


def test_check_fails_on_a_missing_translation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def paths(language: str) -> tuple[Path, Path]:
        return tmp_path / "django.po", tmp_path / "django.mo"

    monkeypatch.setattr("apps.common.management.commands.update_messages.catalogue_paths", paths)
    call_command("update_messages", stdout=io.StringIO())  # writes empty translations

    with pytest.raises(CommandError, match="no en translation"):
        call_command("update_messages", "--check", stdout=io.StringIO())

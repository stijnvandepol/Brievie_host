from argparse import ArgumentParser
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.common import catalogue

# Every language but the source language (Dutch) has a catalogue.
LANGUAGES = ("en",)


def catalogue_paths(language: str) -> tuple[Path, Path]:
    folder = settings.BASE_DIR / "locale" / language / "LC_MESSAGES"
    return folder / "django.po", folder / "django.mo"


def render(language: str) -> tuple[list[catalogue.Message], str, bytes]:
    """The merged messages, and the .po and .mo they make."""
    po_path, _mo_path = catalogue_paths(language)
    existing = catalogue.read_po(po_path.read_text("utf-8")) if po_path.is_file() else []
    messages = catalogue.merge(catalogue.extract(settings.BASE_DIR), existing)
    po = catalogue.write_po(messages, language=language)
    return messages, po, catalogue.write_mo(messages, language=language)


class Command(BaseCommand):
    help = (
        "Bring the translation catalogues (locale/<language>/LC_MESSAGES) up to date with "
        "the Dutch texts in the code (--check: only compare)."
    )
    # Reads and writes files only: no database, so no system checks (they open one).
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit with status 1 when a catalogue is out of date or misses a translation.",
        )

    def handle(self, *args: object, check: bool = False, **options: object) -> None:
        for language in LANGUAGES:
            po_path, mo_path = catalogue_paths(language)
            messages, po, mo = render(language)
            missing = [m.msgid for m in messages if not m.translated]
            if check:
                current_po = po_path.read_text("utf-8") if po_path.is_file() else None
                current_mo = mo_path.read_bytes() if mo_path.is_file() else None
                if current_po != po or current_mo != mo:
                    raise CommandError(
                        f"The {language} catalogue is out of date; run "
                        "`make messages`, translate and commit it.",
                        returncode=1,
                    )
                if missing:
                    raise CommandError(
                        f"{len(missing)} text(s) have no {language} translation, "
                        f"the first: {missing[0]!r}",
                        returncode=1,
                    )
                self.stdout.write(f"The {language} catalogue is up to date.")
                continue
            po_path.parent.mkdir(parents=True, exist_ok=True)
            po_path.write_text(po, encoding="utf-8")
            mo_path.write_bytes(mo)
            self.stdout.write(
                f"Wrote {po_path.relative_to(settings.BASE_DIR)}: {len(messages)} texts, "
                f"{len(missing)} without a translation."
            )

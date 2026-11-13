import json
from argparse import ArgumentParser
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from brievie.api import api

# The contract with Brievie_ios; generated, never edited by hand.
OUTPUT: Path = settings.BASE_DIR.parent / "docs" / "api" / "openapi.json"


def render_schema() -> str:
    schema = api.get_openapi_schema()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


class Command(BaseCommand):
    help = "Write the OpenAPI schema to docs/api/openapi.json (--check: only compare)."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit with status 1 when the committed file differs from the schema.",
        )

    def handle(self, *args: object, check: bool = False, **options: object) -> None:
        rendered = render_schema()
        if check:
            current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else None
            if current != rendered:
                raise CommandError(
                    f"{OUTPUT.name} is out of date; run `make openapi` and commit it.",
                    returncode=1,
                )
            self.stdout.write(f"{OUTPUT.name} is up to date.")
            return
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(rendered, encoding="utf-8")
        self.stdout.write(f"Wrote {OUTPUT}.")

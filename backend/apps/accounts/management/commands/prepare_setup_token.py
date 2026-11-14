from argparse import ArgumentParser

from django.core.management.base import BaseCommand

from apps.accounts.services import prepare_setup_token


class Command(BaseCommand):
    help = (
        "Generate the first-run installation code if the server is not set up yet and "
        "BRIEVIE_SETUP_TOKEN is unset. Runs when the api container starts."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--new", action="store_true", help="Replace an existing code (lost log)."
        )

    def handle(self, *args: object, new: bool = False, **options: object) -> None:
        token = prepare_setup_token(new=new)
        if token is not None:
            # The one place a secret is written to a log (DECISIONS): the server
            # owner needs it once to set the server up; it stops working afterwards.
            self.stdout.write(f"Installatiecode: {token}")
            self.stdout.write(
                "Gebruik deze code één keer om Brievie in te stellen. "
                "Kwijt? Voer `python manage.py prepare_setup_token --new` uit in de api-container."
            )

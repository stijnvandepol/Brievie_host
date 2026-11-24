import sys
from argparse import ArgumentParser

from django.core.management.base import BaseCommand

from apps.installer import restore_install, restore_runtime, runtime


class Command(BaseCommand):
    help = (
        "Restore a back-up on a compose install (run in the admin container, after "
        "`docker compose stop api worker keeper caddy`; the master key of the back-up must be "
        "the one in the vault). Exit 0: restored and checked; 1: refused or failed."
    )

    # The database may be empty or absent: the system checks would open it.
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "run", nargs="?", default=None, help="A run id or pre-update-X.Y.Z (default: newest)."
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Replace the data of this installation (needed when there is any).",
        )

    def handle(self, *args: object, **options: object) -> None:
        chosen = options["run"]
        try:
            steps = restore_runtime.build_steps(runtime.own_environ())
            code = restore_install.run(
                steps,
                run_id=None if chosen is None else str(chosen),
                replace=bool(options["replace"]),
                say=self.stdout.write,
                complain=self.stderr.write,
            )
        except (runtime.ConfigurationError, restore_install.StepError) as error:
            self.stderr.write(str(error))
            code = 1
        if code:
            sys.exit(code)

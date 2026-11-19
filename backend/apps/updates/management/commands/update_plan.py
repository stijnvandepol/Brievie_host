import sys
from typing import IO

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.updates.release import (
    REASONS,
    ReleaseError,
    Version,
    decide,
    parse_release_stream,
    refusal_message,
)


class Command(BaseCommand):
    help = (
        "Whether ./brievie update may go from --installed to --target. Reads the releases "
        "on stdin ('--- vX.Y.Z' and that tag's release.json, per tag). Prints 'allowed' or "
        "'already' (exit 0); a refusal is a Dutch sentence (exit 1); a bad argument exit 2. "
        "Needs no database."
    )
    # Runs under `docker run --network none`: no system checks, no database.
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute
    stealth_options = ("stdin",)

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--installed", required=True)
        parser.add_argument("--target", required=True)
        parser.add_argument("--postgres-major", required=True)

    def handle(self, *args: object, **options: object) -> None:
        try:
            installed = Version.parse(str(options["installed"]))
            target = Version.parse(str(options["target"]))
        except ReleaseError:
            raise CommandError(str(REASONS["version_invalid"]), returncode=2) from None
        major_text = str(options["postgres_major"])
        if not major_text.isascii() or not major_text.isdigit():
            raise CommandError("--postgres-major is a number.", returncode=2)
        stdin = options.get("stdin")
        source: IO[str] = stdin if hasattr(stdin, "read") else sys.stdin  # type: ignore[assignment]
        releases = parse_release_stream(source.read())
        decision = decide(
            installed=installed,
            target=target,
            releases=releases,
            postgres_major=int(major_text),
        )
        if decision.outcome == "refused":
            message = refusal_message(
                decision, installed=installed, target=target, postgres_major=int(major_text)
            )
            raise CommandError(message, returncode=1)
        self.stdout.write(decision.outcome)

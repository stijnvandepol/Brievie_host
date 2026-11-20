import sys
from typing import IO

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.updates.release import (
    REASONS,
    Release,
    ReleaseEntry,
    ReleaseError,
    Version,
    decide,
    parse_release_stream,
    refusal_message,
)


class Command(BaseCommand):
    help = (
        "Whether ./brievie update may go from --installed to --target (default: the newest "
        "release that can be read). Reads the releases on stdin ('--- vX.Y.Z' and that "
        "tag's release.json, per tag). Prints 'target=X.Y.Z' and then 'allowed' or "
        "'already' (exit 0); a refusal is a Dutch sentence (exit 1); a bad argument exit 2. "
        "Needs no database."
    )
    # Runs under `docker run --network none`: no system checks, no database.
    requires_system_checks: list[str] = []  # noqa: RUF012 - Django's own attribute
    stealth_options = ("stdin",)

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--installed", required=True)
        parser.add_argument("--target", default=None)
        parser.add_argument("--postgres-major", required=True)

    def handle(self, *args: object, **options: object) -> None:
        try:
            installed = Version.parse(str(options["installed"]))
            typed = options["target"]
            target = Version.parse(str(typed)) if typed is not None else None
        except ReleaseError:
            raise CommandError(str(REASONS["version_invalid"]), returncode=2) from None
        major_text = str(options["postgres_major"])
        if not major_text.isascii() or not major_text.isdigit():
            raise CommandError("--postgres-major is a number.", returncode=2)
        stdin = options.get("stdin")
        source: IO[str] = stdin if hasattr(stdin, "read") else sys.stdin  # type: ignore[assignment]
        releases = parse_release_stream(source.read())
        if target is None:
            target = newest_readable(releases, installed=installed)
            for version in sorted(releases):
                if version > target and isinstance(releases[version], ReleaseError):
                    self.stderr.write(
                        f"Versie {version} is overgeslagen: de gegevens van die versie "
                        "(deploy/release.json) kloppen niet. Typ ./brievie update met een "
                        "versie als je die toch wilt proberen."
                    )
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
        self.stdout.write(f"target={target}")
        self.stdout.write(decision.outcome)


def newest_readable(releases: dict[Version, ReleaseEntry], *, installed: Version) -> Version:
    """The highest release whose release.json could be read, or `installed` when none is
    newer (the answer is then "already")."""
    readable = [version for version, entry in releases.items() if isinstance(entry, Release)]
    return max([installed, *readable])

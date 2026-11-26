"""Restore a back-up on a compose install, in the admin container (no wrapper, no terminal
on the host beyond one `docker compose run`).

The wrapper's `./brievie restore` stops the services itself; a container has no way to
stop its neighbours, so here the person stops them first and this refuses while any of them
still holds a database session. The steps are those of the wrapper: open the back-up with
the master key of this installation (put the sheet's key in the vault first when this is a
new machine), check the run is complete, make an empty database, load the dump, put the
documents and the certificate authority back, give the data this installation's id, and
check the result against the back-up's manifest.

`run` takes its steps as a `Steps` value, so every refusal and the order are tested without
a database; `apps.installer.restore_runtime` builds the real ones.
"""

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from django.utils.translation import gettext as _

from apps.backup import safepaths

STAGING = Path("/out")
# The two files a restore writes to its staging folder (apps.backup.restore.fetch); they hold
# the master key and are removed as soon as they are not needed.
STAGING_FILES = ("env", "manifest.json")
ADMIN = "docker compose --profile admin run --rm admin python manage.py"
COMMAND = f"{ADMIN} restore_install"
STOP_COMMAND = "docker compose stop api worker intake keeper caddy"
START_COMMAND = "docker compose up -d"


def restore_commands(label: str, backup_dir: str = "update-backup") -> list[str]:
    """The three commands of the way back (or a restore): stop, restore, start again.

    One source for the sentence `init` prints after a failed update and for
    docs/SELFHOST.md (a test compares them). `backup_dir` is where the back-up is: the
    volume `update-backup` for the emergency back-up of an update without a back-up folder."""
    return [
        STOP_COMMAND,
        f"BRIEVIE_BACKUP_DIR={backup_dir} {COMMAND} {label} --replace",
        START_COMMAND,
    ]


class StepError(Exception):
    """A step failed; the sentence says what and holds no secret."""

    def __init__(self, sentence: str) -> None:
        super().__init__(sentence)
        self.sentence = sentence


@dataclass(frozen=True)
class Steps:
    open_sessions: Callable[[], int]
    has_data: Callable[[], bool]
    fetch: Callable[[str | None], str]  # the run id of the back-up that was opened
    check: Callable[[str], None]
    reset_database: Callable[[], None]
    load_database: Callable[[str], None]
    load_files: Callable[[str], None]
    adopt_id: Callable[[], None]
    verify: Callable[[], str]  # one sentence about what was checked
    clean_staging: Callable[[], None]


def run(
    steps: Steps,
    *,
    run_id: str | None,
    replace: bool,
    say: Callable[[str], None],
    complain: Callable[[str], None],
) -> int:
    """0: restored and checked; 1: refused or failed (the sentence says what; before the
    database is reset nothing has changed)."""
    if steps.open_sessions():
        complain(
            _(
                "Brievie draait nog. Stop eerst de services die de database gebruiken: "
                "docker compose stop api worker intake keeper caddy. Er is niets veranderd."
            )
        )
        return 1
    if steps.has_data() and not replace:
        complain(
            _(
                "Er staan al gegevens in deze installatie. Wil je ze vervangen door de "
                "back-up? Typ het commando opnieuw met --replace. Er is niets veranderd."
            )
        )
        return 1
    try:
        say(_("De back-up wordt geopend…"))
        found = steps.fetch(run_id)
        steps.check(found)
        # From here the old data is replaced: the sentences say how far it got.
        say(_("De database wordt vervangen…"))
        steps.reset_database()
        steps.load_database(found)
        say(_("De documenten worden teruggezet…"))
        steps.load_files(found)
        steps.adopt_id()
        say(_("Het herstel wordt gecontroleerd…"))
        say(steps.verify())
    except StepError as error:
        steps.clean_staging()
        complain(error.sentence)
        return 1
    steps.clean_staging()
    say(
        _(
            "Herstel gelukt. Start Brievie nu weer: docker compose up -d. "
            "Een telefoon die al gekoppeld was, blijft werken."
        )
    )
    return 0


def remove_staging(directory: Path) -> int:
    """Remove the files a restore left in its staging folder, and nothing else.

    Refuses (`unsafe_path`) any folder but `/out` or one in the test root, a link, and
    every entry that is not a regular file of the fixed list; returns how many it removed.
    """
    safepaths.out_dir_rule(directory)
    descriptor = safepaths.open_dir_nofollow(directory)
    removed = 0
    try:
        for name in STAGING_FILES:
            try:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                os.unlink(name, dir_fd=descriptor)
                removed += 1
    finally:
        os.close(descriptor)
    return removed

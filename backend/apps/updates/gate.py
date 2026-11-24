"""The start gate of a compose install: may this image start on this data? (S 4.6)

Pure: no Django import, no database, no files. `./brievie update` checks the jump, the
PostgreSQL major, the space and the back-up before anything changes; with only a compose
file nothing runs first, so the one-shot `init` container asks this function. It reuses
the sentences of `apps.updates.release` (`refusal_message`).
"""

from dataclasses import dataclass
from typing import Literal

from apps.updates.release import Release, Version

# What this image knows of the compose file's structure. Raise it (with the same number in
# deploy/Dockerfile and in the compose file) when a release needs a new service or volume.
COMPOSE_REVISION = 1


@dataclass(frozen=True)
class GateDecision:
    outcome: Literal["fresh", "same", "update", "refused"]
    code: str = ""
    go_first: Version | None = None


def _refused(code: str, go_first: Version | None = None) -> GateDecision:
    return GateDecision("refused", code, go_first)


def decide_start(
    *,
    image: Version,
    installed: Version | None,
    release: Release,
    database_major: int | None,
    unknown_migrations: bool,
    compose_revision: int,
    image_compose_revision: int,
    adopt_version: Version | None,
    has_data: bool,
    free_bytes: int,
    needed_bytes: int,
) -> GateDecision:
    """The rules in the order of S 4.6; the first that refuses wins.

    1. an older image than the data, or migrations the image does not know;
    2. a jump past `release.min_upgrade_from`;
    3. another PostgreSQL major than the image is built for;
    4. a compose file of another revision than the image knows;
    5. too little free space (only for an update, which makes the emergency back-up).
    """
    if unknown_migrations or (installed is not None and image < installed):
        return _refused("unknown_migrations" if unknown_migrations else "downgrade")
    if installed is None and has_data:
        if adopt_version is None:
            return _refused("adopt_needed")
        if adopt_version != image:
            return _refused("adopt_differs")
        installed = image
    if installed is not None and installed != image and release.min_upgrade_from > installed:
        return _refused("jump_too_large", go_first=release.min_upgrade_from)
    if database_major is not None and database_major != release.postgres_major:
        return _refused("postgres_major_differs")
    if compose_revision < image_compose_revision:
        return _refused("compose_too_old")
    if compose_revision > image_compose_revision:
        return _refused("compose_too_new")
    if installed is None:
        return GateDecision("fresh")
    if installed == image:
        return GateDecision("same")
    if free_bytes < needed_bytes:
        return _refused("no_space")
    return GateDecision("update")

"""The rules of `./brievie update`: versions, `deploy/release.json` and the allowed jump.

Pure: no database, no files, no Django but the lazy Dutch texts. The updater reads the
target's `release.json` from its tag before anything changes (design 7.3); a release
that needs another updater says so with a `format` this code does not know.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

# Every installation from before versions reports this version (pyproject said so).
PRE_VERSIONS_TEXT = "0.1.0"
RELEASE_FORMAT = 1
# Room for building the target's image (layers, the web build, the virtualenv).
BUILD_RESERVE_BYTES = 4 * 1024**3

# ASCII digits only (`\d` would take any script's digits); at most four per part.
_PART = r"(0|[1-9][0-9]{0,3})"
_VERSION = re.compile(rf"v?{_PART}\.{_PART}\.{_PART}")
_HEADER = re.compile(r"--- (.*)")
_RELEASE_KEYS = frozenset({"format", "version", "min_upgrade_from", "postgres_major"})
_POSTGRES_MAJORS = range(10, 100)


class ReleaseError(Exception):
    """A refusal by code; the sentence is in REASONS."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> "Version":
        """`X.Y.Z` or `vX.Y.Z`, nothing around it; anything else `version_invalid`."""
        match = _VERSION.fullmatch(text)
        if match is None:
            raise ReleaseError("version_invalid")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


PRE_VERSIONS = Version.parse(PRE_VERSIONS_TEXT)


@dataclass(frozen=True)
class Release:
    version: Version
    min_upgrade_from: Version
    postgres_major: int


ReleaseEntry = Release | ReleaseError


@dataclass(frozen=True)
class Decision:
    outcome: Literal["allowed", "already", "refused"]
    code: str = ""
    go_first: Version | None = None


def _plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _version_field(value: object) -> Version:
    if not isinstance(value, str):
        raise ReleaseError("release_invalid")
    try:
        return Version.parse(value)
    except ReleaseError:
        raise ReleaseError("release_invalid") from None


def parse_release(text: str) -> Release:
    """One `deploy/release.json`. A `format` other than 1 is `release_format_unknown`
    (a later release that this updater cannot do); any other fault `release_invalid`."""
    try:
        data = json.loads(text)
    except ValueError:
        raise ReleaseError("release_invalid") from None
    if not isinstance(data, dict):
        raise ReleaseError("release_invalid")
    if "format" not in data:
        raise ReleaseError("release_invalid")
    if not _plain_int(data["format"]) or data["format"] != RELEASE_FORMAT:
        raise ReleaseError("release_format_unknown")
    if set(data) != _RELEASE_KEYS:
        raise ReleaseError("release_invalid")
    version = _version_field(data["version"])
    minimum = _version_field(data["min_upgrade_from"])
    major = data["postgres_major"]
    if not _plain_int(major) or major not in _POSTGRES_MAJORS:
        raise ReleaseError("release_invalid")
    if minimum > version:
        raise ReleaseError("release_invalid")
    return Release(version=version, min_upgrade_from=minimum, postgres_major=major)


def parse_release_stream(text: str) -> dict[Version, ReleaseEntry]:
    """Records as ./brievie prints them: a line `--- vX.Y.Z`, then that tag's release.json.

    A record whose release names another version than its header is `release_mismatch`;
    a malformed header line and its record are skipped.
    """
    records: dict[Version, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        header = _HEADER.fullmatch(line)
        if header is not None:
            try:
                version = Version.parse(header.group(1))
            except ReleaseError:
                current = None
                continue
            current = records.setdefault(version, [])
            current.clear()
        elif current is not None:
            current.append(line)
    parsed: dict[Version, ReleaseEntry] = {}
    for version, lines in records.items():
        try:
            release = parse_release("\n".join(lines))
        except ReleaseError as error:
            parsed[version] = error
            continue
        parsed[version] = (
            release if release.version == version else ReleaseError("release_mismatch")
        )
    return parsed


def decide(
    *,
    installed: Version,
    target: Version,
    releases: Mapping[Version, ReleaseEntry],
    postgres_major: int,
) -> Decision:
    """Whether `./brievie update` may go from `installed` to `target` in one step."""
    if installed <= PRE_VERSIONS:
        return Decision("refused", "before_versions")
    if target == installed:
        return Decision("already")
    if target < installed:
        return Decision("refused", "downgrade")
    entry = releases.get(target)
    if entry is None:
        return Decision("refused", "release_missing")
    if isinstance(entry, ReleaseError):
        return Decision("refused", entry.code)
    if entry.postgres_major != postgres_major:
        return Decision("refused", "postgres_major_changed")
    if entry.min_upgrade_from <= installed:
        return Decision("allowed")
    steps = [
        release.version
        for release in releases.values()
        if isinstance(release, Release)
        and release.min_upgrade_from <= installed < release.version < target
        and release.postgres_major == postgres_major
    ]
    if steps:
        return Decision("refused", "go_first", go_first=max(steps))
    return Decision("refused", "no_path")


def space_needed(*, documents_bytes: int, database_bytes: int, fallback: bool) -> int:
    """Free bytes an update needs: the build, plus a copy of the data for the fallback."""
    return BUILD_RESERVE_BYTES + (documents_bytes + database_bytes if fallback else 0)


NOTHING_CHANGED = _("Er is niets veranderd.")

# Dutch (B1). {installed}, {target}, {go_first} and {postgres_major} are filled in.
REASONS: dict[str, Promise] = {
    "version_invalid": _("Een versie ziet eruit als 0.3.0."),
    "before_versions": _(
        "Deze installatie is van vóór de versienummers. Werk één keer met de hand bij: "
        "git fetch --tags, git checkout v{target}, ./brievie start. Daarna werkt ./brievie update."
    ),
    "downgrade": _(
        "Een oudere versie installeren kan niet. Terug ga je met een herstel van de back-up "
        "van vóór het bijwerken."
    ),
    "release_missing": _(
        "Versie {target} bestaat niet: er is geen tag v{target} met een release.json."
    ),
    "release_invalid": _("De gegevens van versie {target} (deploy/release.json) kloppen niet."),
    "release_mismatch": _(
        "De tag v{target} hoort niet bij versie {target}: "
        "zijn release.json noemt een andere versie."
    ),
    "release_format_unknown": _(
        "Versie {target} moet op een nieuwere manier worden bijgewerkt dan deze versie kent. "
        'Lees "Voor je bijwerkt" in CHANGELOG.md.'
    ),
    "postgres_major_changed": _(
        "Versie {target} gebruikt een andere hoofdversie van PostgreSQL dan deze server "
        "({postgres_major}). Die overstap kan ./brievie update nog niet maken. "
        'Lees "Voor je bijwerkt" in CHANGELOG.md.'
    ),
    "go_first": _(
        "Van versie {installed} kun je niet in één keer naar {target}. "
        "Werk eerst bij naar {go_first}: ./brievie update {go_first}"
    ),
    "no_path": _(
        "Van versie {installed} is er geen weg naar {target} met ./brievie update. "
        'Lees "Voor je bijwerkt" in CHANGELOG.md.'
    ),
}


def refusal_message(
    decision: Decision, *, installed: Version, target: Version, postgres_major: int
) -> str:
    """The Dutch sentence for a refused jump, ending in "Er is niets veranderd."."""
    template = str(REASONS.get(decision.code, REASONS["release_invalid"]))
    text = template.format(
        installed=installed,
        target=target,
        go_first=decision.go_first or "",
        postgres_major=postgres_major,
    )
    # A sentence that ends in a command keeps it alone on its line, to copy it.
    separator = " " if text.endswith(".") else "\n"
    return f"{text}{separator}{NOTHING_CHANGED}"

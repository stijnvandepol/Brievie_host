"""The scan folder's rules (intake spec S 3.2, 3.2a, 3.3). Pure: no Django, no database,
no files. Which names are read, when a file is still being written, what a person's or
department's folder is called and which folders exist."""

import re
import unicodedata
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

RESERVED = "mislukt"
_RESERVED_PREFIXES = (".", "@", "#", "~")
_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png")
FOLDER_NAME_MAX = 60
_VALID_FOLDER_NAME = re.compile(r"[a-z0-9-]{1,60}")
# Not allowed in an SMB name, plus control characters and the marks that turn text around.
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069]')
_SPACES = re.compile(r"\s+")
# Fixed for ever: it makes a scan's client id, which must stay the same across versions.
SCAN_NAMESPACE = uuid.UUID("5b0f6a3e-1f0c-4d6e-9a43-2c8e7d1b9f50")

# A path on the share: name components relative to the scan root, never a joined string.
type RelPath = tuple[str, ...]


@dataclass(frozen=True)
class Fingerprint:
    """What identifies one version of one file: where it lives and what it looks like."""

    device: int
    inode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class FolderCandidate:
    """A person or department and their folder's current state (None: no folder yet)."""

    id: uuid.UUID
    kind: str
    display_name: str
    archived: bool
    has_account: bool
    added_by_admin: bool
    state: str | None


# Person or department id to the state its folder should have.
type FolderPlan = dict[uuid.UUID, str]


def is_reserved(name: str) -> bool:
    """Never a person's or department's folder: `mislukt`, and the NAS's own folders
    (`.`, `@eaDir`, `#recycle`, `~`)."""
    stripped = name.strip()
    return stripped.casefold() == RESERVED or stripped.startswith(_RESERVED_PREFIXES)


def is_candidate_name(name: str) -> bool:
    """A file the job may read: PDF, JPEG or PNG by its name, not hidden, not a temporary
    file of a scanner or an Office program. The content decides the kind later."""
    if name.startswith((".", "~")):
        return False
    return name.lower().endswith(_EXTENSIONS)


def stable(
    previous: Fingerprint | None,
    stable_since: datetime | None,
    current: Fingerprint,
    now: datetime,
    *,
    stable_seconds: int,
) -> tuple[bool, datetime]:
    """(take it, since when it looks like this). A file is taken when size and mtime were
    the same at two looks at least `stable_seconds` apart; an empty file never."""
    changed = (
        previous is None
        or stable_since is None
        or (previous.size, previous.mtime_ns) != (current.size, current.mtime_ns)
    )
    if changed or stable_since is None:
        return False, now
    if current.size == 0:
        return False, stable_since
    return now - stable_since >= timedelta(seconds=stable_seconds), stable_since


def valid_folder_name(name: str) -> bool:
    """A workspace folder name: lower case letters, digits and `-` (SMB ignores case, so
    two names may not differ by it), at most 60, not reserved."""
    return bool(_VALID_FOLDER_NAME.fullmatch(name)) and not is_reserved(name)


def _ascii(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def default_folder_name(workspace_name: str) -> str:
    """The workspace's name as a folder name: "Kantoor Den Haag" is `kantoor-den-haag`."""
    name = re.sub(r"[^a-z0-9]+", "-", _ascii(workspace_name).lower()).strip("-")
    name = name[:FOLDER_NAME_MAX].strip("-")
    return name if valid_folder_name(name) else "brievie"


def _safe(display_name: str) -> str:
    name = _SPACES.sub(" ", _UNSAFE.sub("", display_name)).strip()
    # Windows and SMB drop a trailing dot or space; a leading mark makes a reserved name.
    name = name.lstrip("".join(_RESERVED_PREFIXES) + " ").rstrip(". ")
    return name[:FOLDER_NAME_MAX].rstrip(". ") or "map"


def valid_person_folder_name(name: str) -> bool:
    """A name an admin gives a person's or department's folder: already safe for SMB (no
    character `person_folder_names` would remove), at most 60, not reserved."""
    return bool(name) and _safe(name) == name and not is_reserved(name)


def _numbered(base: str, number: int) -> str:
    suffix = f" ({number})"
    return base[: FOLDER_NAME_MAX - len(suffix)].rstrip(". ") + suffix


def person_folder_names(
    names: Mapping[uuid.UUID, str], *, taken: Mapping[uuid.UUID, str]
) -> dict[uuid.UUID, str]:
    """A folder name per id: persons and departments share one namespace on disk.

    An id in `taken` keeps its name (a renamed person keeps their folder). A new name is
    the display name made safe for SMB, unique ignoring case against every taken name and
    every name given before it, in id order: the second "Anne" is "anne (2)"."""
    result: dict[uuid.UUID, str] = {}
    used = {name.casefold() for name in taken.values()}
    for person_id in sorted(names):
        if person_id in taken:
            result[person_id] = taken[person_id]
            continue
        base = _safe(names[person_id])
        name, number = base, 1
        while is_reserved(name) or name.casefold() in used:
            number += 1
            name = _numbered(base, number)
        used.add(name.casefold())
        result[person_id] = name
    return result


def folder_targets(persons: Sequence[FolderCandidate], *, departments_enabled: bool) -> FolderPlan:
    """The state each folder should have (spec S 3.2a). A person linked to an account, or
    added by an admin, is active; a department is active while departments are on and
    paused while they are off; one archived or no longer qualifying is gone (only when it
    has a folder); a folder an admin stopped stays stopped. Ids that need no folder are
    left out."""
    plan: FolderPlan = {}
    for candidate in persons:
        if candidate.state == "stopped":
            plan[candidate.id] = "stopped"
            continue
        if candidate.archived:
            qualifies: str | None = None
        elif candidate.kind == "department":
            qualifies = "active" if departments_enabled else "paused"
        elif candidate.has_account or candidate.added_by_admin:
            qualifies = "active"
        else:
            qualifies = None
        if qualifies is not None:
            plan[candidate.id] = qualifies
        elif candidate.state is not None:
            plan[candidate.id] = "gone"
    return plan


def classify_subfolders(
    names_on_disk: Collection[str], known: Mapping[str, str]
) -> tuple[dict[str, str], list[str]]:
    """(state per directory on disk, directories that map to nobody). `known` maps a
    folder's name in lower case to its state; reserved names are skipped."""
    folded = {name.casefold(): state for name, state in known.items()}
    states: dict[str, str] = {}
    unknown: list[str] = []
    for name in names_on_disk:
        if is_reserved(name):
            continue
        state = folded.get(name.casefold())
        if state is None:
            unknown.append(name)
        else:
            states[name] = state
    return states, sorted(unknown)


def scan_client_id(workspace_id: uuid.UUID, path: RelPath, sha256: str, mtime_ns: int) -> uuid.UUID:
    """The letter's `client_id`: the same file found again after a crash between the
    import and its removal answers the existing document instead of making a second."""
    # A component holds no "/", so the joined path is unambiguous.
    return uuid.uuid5(SCAN_NAMESPACE, f"{workspace_id}/{'/'.join(path)}/{sha256}/{mtime_ns}")

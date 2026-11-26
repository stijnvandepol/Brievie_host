"""The scan folder's round (intake spec S 3.2a, 3.3): every minute, per workspace with the
scan folder on, look at its folder on the share, take the files that stopped changing and
make each one a letter, then remove the source after a verified import.

Every file operation goes through `apps.intake.scan_files`. The database is touched in
short transactions (`workspace_context`, as the system: the job is nobody); none is open
while the share is read, a file is checked or the original is read back. Logs hold codes,
counts and document ids only: never a file or folder name.
"""

import contextlib
import hashlib
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

from apps.common import crypto
from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.documents import intake as document_intake
from apps.documents.models import Document
from apps.documents.schemas import FileMetadataIn
from apps.documents.storage import default_storage
from apps.intake import scan_files
from apps.intake.files import CheckedFile, FileRefusedError, check_file
from apps.intake.models import ScanFile, ScanFolder, ScanFolderSettings
from apps.intake.scan_files import Fingerprint, ImportedFile, RelPath, ScanRefusedError
from apps.intake.scan_rules import (
    RESERVED,
    FolderCandidate,
    classify_subfolders,
    folder_targets,
    person_folder_names,
    scan_client_id,
    stable,
)
from apps.intake.services import PersonGoneError, create_document_from_file
from apps.people.models import Person
from apps.pipeline.errors import PermanentError
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Workspace

logger = logging.getLogger(__name__)

ROUND_LIMIT = 20
PURGE_AFTER = timedelta(days=30)
UNKNOWN_FOLDERS_MAX = 50
# What one round looks at (security review of PR #64, M4): candidates per folder, waiting
# rows per workspace, scan letters still being read before more are taken in, and how long
# a file may stay empty or keep changing before it goes to mislukt/.
CANDIDATES_PER_FOLDER = 500
ROWS_MAX = 2000
PROCESSING_MAX = 50
NEVER_SETTLED_AFTER = timedelta(hours=1)
RELATIVE_PATH_MAX = 400
# The reason of an imported row whose source could not be removed, and when to try again.
REMOVE_FAILED = "remove_failed"
REMOVE_RETRY = timedelta(hours=1)

# The sentence in `<name>.reden.txt` beside a file in mislukt/, per refusal code.
REASON_TEXTS: Mapping[str, Promise] = {
    "file_type_not_supported": _(
        "Brievie kon dit bestand niet lezen: het is geen PDF, JPG of PNG."
    ),
    "file_too_large": _("Brievie kon dit bestand niet lezen: het is te groot."),
    "too_many_pages": _("Brievie kon dit bestand niet lezen: de PDF heeft te veel pagina's."),
    "file_encrypted": _(
        "Brievie kon dit bestand niet lezen: de PDF is beveiligd met een wachtwoord."
    ),
    "file_unreadable": _("Brievie kon dit bestand niet lezen: het is beschadigd of onvolledig."),
    "image_too_large": _("Brievie kon dit bestand niet lezen: de afbeelding is te groot."),
    "person_gone": _(
        "Brievie heeft dit bestand niet opgehaald: de persoon of afdeling van deze map is er "
        "niet meer. Zet het bestand in een andere map."
    ),
    "unreadable": _("Brievie kon dit bestand niet verwerken."),
    "never_settled": _(
        "Brievie heeft dit bestand niet opgehaald: het bleef leeg of veranderde een uur lang."
    ),
}

_logged_root_refusals: set[str] = set()


@dataclass(frozen=True)
class _Folder:
    """A subfolder of the workspace folder as the database knows it."""

    name: str
    state: str
    person_id: UUID | None


@dataclass(frozen=True)
class _Config:
    folder_name: str
    per_person: bool
    folders: dict[str, _Folder] = field(default_factory=dict)


@dataclass(frozen=True)
class _Sighting:
    path: RelPath
    fingerprint: Fingerprint
    # The folder's person or department; None for the workspace folder itself.
    person_id: UUID | None
    # "active", "paused" or "gone".
    state: str


def scan_round(*, now: Callable[[], datetime] = timezone.now) -> None:
    """One round over every workspace. A root the guards refuse logs its code once per
    process and nothing else happens."""
    try:
        root_fd = scan_files.open_root()
    except ScanRefusedError as refusal:
        if refusal.code not in _logged_root_refusals:
            _logged_root_refusals.add(refusal.code)
            if refusal.code != "root_unset":
                logger.warning("Scan folder refused: %s", refusal.code)
        return
    try:
        for workspace_id in Workspace.objects.values_list("id", flat=True):
            try:
                scan_workspace(workspace_id, root_fd, now=now())
                _purge(workspace_id, now())
            except Exception as error:  # noqa: BLE001 - one workspace never stops the others
                # The type or code only: a message may quote a file's name.
                code = error.code if isinstance(error, ScanRefusedError) else type(error).__name__
                logger.warning("Scan folder round stopped for %s: %s", workspace_id, code)
                _store_failing(workspace_id, now())
    finally:
        os.close(root_fd)


def _store_failing(workspace_id: UUID, now: datetime) -> None:
    """The page says the round broke off, instead of the last good look's "werkt"."""
    try:
        with workspace_context(workspace_id, actor=SYSTEM):
            ScanFolderSettings.objects.filter(workspace_id=workspace_id, enabled=True).update(
                check_state="failing", checked_at=now, updated_at=now
            )
    except DatabaseError:
        logger.warning("Scan folder state not stored for %s", workspace_id)


def _purge(workspace_id: UUID, now: datetime) -> None:
    with workspace_context(workspace_id, actor=SYSTEM):
        ScanFile.objects.filter(
            state=ScanFile.State.IMPORTED, removed_at__lt=now - PURGE_AFTER
        ).delete()


def scan_workspace(
    workspace_id: UUID, root_fd: int, *, now: datetime, limit: int = ROUND_LIMIT
) -> None:
    """One round for one workspace (see the module)."""
    config = _prepare(workspace_id)
    if config is None:
        return
    top: RelPath = (config.folder_name,)
    # Claimed before anything is written in it: a folder of another workspace, or an
    # existing one with something in it, gets no mislukt/ and no probe file.
    try:
        claim = scan_files.claim_folder(root_fd, top, str(workspace_id))
    except ScanRefusedError:
        claim = None
    if claim is None:
        _store_probe(workspace_id, scan_files.probe(root_fd, top), now=now)
        return
    if claim != "ours":
        _store_probe(workspace_id, scan_files.ProbeResult(claim, 0, 0), now=now)
        return
    # When this fails, the probe says what is wrong.
    with contextlib.suppress(ScanRefusedError):
        scan_files.ensure_folder(root_fd, (*top, RESERVED))
    checked = scan_files.probe(root_fd, top)
    if checked.state in ("missing", "not_readable"):
        _store_probe(workspace_id, checked, now=now)
        return
    for folder in config.folders.values():
        if folder.state == ScanFolder.State.ACTIVE:
            try:
                scan_files.ensure_folder(root_fd, (*top, folder.name))
            except ScanRefusedError:
                continue
    _store_probe(workspace_id, checked, now=now)
    sightings, unknown, over = _look(root_fd, config)
    failed_names = scan_files.list_failed(root_fd, top)
    found = _record(workspace_id, config, sightings, unknown, failed_names, now=now, skipped=over)
    for row_id, imported in found.removable[:limit]:
        _verify_and_remove(workspace_id, root_fd, row_id, imported, data=None)
    for sighting in found.unsettled[:limit]:
        _fail(workspace_id, root_fd, sighting, "never_settled")
    # The pipeline has enough of this workspace's scans to read: the rest waits.
    room = min(limit, PROCESSING_MAX - _processing(workspace_id))
    # Only what was taken in or moved counts; a file that could do neither (no rights to
    # move it) is tried again next round but never holds up the files behind it.
    done = 0
    for sighting in found.ready[: limit * 3]:
        if done >= room:
            break
        if _take(workspace_id, root_fd, sighting):
            done += 1


def _store_probe(workspace_id: UUID, checked: scan_files.ProbeResult, *, now: datetime) -> None:
    with workspace_context(workspace_id, actor=SYSTEM):
        ScanFolderSettings.objects.filter(workspace_id=workspace_id).update(
            check_state=checked.state,
            owner_uid=checked.owner_uid,
            owner_gid=checked.owner_gid,
            checked_at=now,
            updated_at=now,
        )


def _prepare(workspace_id: UUID) -> _Config | None:
    """The workspace's settings and, with folders per person, its folders brought up to
    date with its persons and departments (spec S 3.2a). None when the folder is off."""
    with workspace_context(workspace_id, actor=SYSTEM):
        stored = ScanFolderSettings.objects.filter(workspace_id=workspace_id).first()
        if stored is None or not stored.enabled or not stored.folder_name:
            return None
        if not stored.per_person:
            return _Config(folder_name=stored.folder_name, per_person=False)
        departments_enabled = (
            Workspace.objects.filter(id=workspace_id)
            .values_list("departments_enabled", flat=True)
            .first()
        )
        rows = list(ScanFolder.objects.all())
        by_person = {row.person_id: row for row in rows if row.person_id is not None}
        persons = list(
            Person.objects.values_list("id", "kind", "display_name", "archived_at", "user_id")
        )
        candidates = [
            FolderCandidate(
                id=person_id,
                kind=kind,
                display_name=display_name,
                archived=archived_at is not None,
                has_account=user_id is not None,
                added_by_admin=by_person[person_id].added_by_admin
                if person_id in by_person
                else False,
                state=by_person[person_id].state if person_id in by_person else None,
            )
            for person_id, kind, display_name, archived_at, user_id in persons
        ]
        plan = folder_targets(candidates, departments_enabled=bool(departments_enabled))
        for row in rows:
            target = (
                plan.get(row.person_id, ScanFolder.State.GONE)
                if row.person_id is not None
                else ScanFolder.State.GONE
            )
            if row.state != target:
                row.state = target
                row.save(update_fields=["state", "updated_at"])
        new_ids = [
            person_id
            for person_id, target in plan.items()
            if person_id not in by_person and target != ScanFolder.State.GONE
        ]
        if new_ids:
            names = {person_id: display for person_id, _k, display, _a, _u in persons}
            taken = {row.person_id or row.id: row.name for row in rows}
            chosen = person_folder_names(
                {person_id: names[person_id] for person_id in new_ids}, taken=taken
            )
            for person_id in new_ids:
                rows.append(
                    ScanFolder.objects.create(
                        workspace_id=workspace_id,
                        person_id=person_id,
                        name=chosen[person_id],
                        state=plan[person_id],
                    )
                )
        folders = {
            row.name.casefold(): _Folder(name=row.name, state=row.state, person_id=row.person_id)
            for row in rows
        }
        return _Config(folder_name=stored.folder_name, per_person=True, folders=folders)


def _look(root_fd: int, config: _Config) -> tuple[list[_Sighting], list[dict[str, object]], int]:
    """The files in the workspace folder and its folders, the folders nobody owns (name
    masked, file count), and how many files were left out because a folder holds more than
    `CANDIDATES_PER_FOLDER`. Unknown and stopped folders are only counted."""
    top: RelPath = (config.folder_name,)
    over = 0

    def capped(folder: RelPath) -> list[tuple[RelPath, Fingerprint]]:
        nonlocal over
        found = scan_files.list_candidates(root_fd, folder, limit=CANDIDATES_PER_FOLDER + 1)
        if len(found) > CANDIDATES_PER_FOLDER:
            # Names only: counting what is left out must not cost a `stat` per file.
            over += max(scan_files.count_candidates(root_fd, folder) - CANDIDATES_PER_FOLDER, 1)
        return found[:CANDIDATES_PER_FOLDER]

    sightings = [
        _Sighting(path, fingerprint, None, ScanFolder.State.ACTIVE)
        for path, fingerprint in capped(top)
    ]
    known = {name: folder.state for name, folder in config.folders.items()}
    states, unknown_names = classify_subfolders(scan_files.list_subfolders(root_fd, top), known)
    unknown: list[dict[str, object]] = []
    for name, state in sorted(states.items()):
        folder = config.folders[name.casefold()]
        if state == ScanFolder.State.STOPPED:
            unknown_names.append(name)
            continue
        try:
            found = capped((*top, name))
        except ScanRefusedError:
            continue
        sightings.extend(
            _Sighting(path, fingerprint, folder.person_id, state) for path, fingerprint in found
        )
    for name in sorted(unknown_names)[:UNKNOWN_FOLDERS_MAX]:
        unknown.append(
            {
                "name": mask_bsns(name),
                "files": scan_files.count_candidates(root_fd, (*top, name)),
            }
        )
    return sightings, unknown, over


@dataclass
class _Recorded:
    """What a round's bookkeeping found: stable files to take (oldest first), imported
    files still on the share, files that never settled, and how many were skipped."""

    ready: list[_Sighting] = field(default_factory=list)
    removable: list[tuple[UUID, ImportedFile]] = field(default_factory=list)
    unsettled: list[_Sighting] = field(default_factory=list)
    skipped: int = 0


def _fits(sighting: _Sighting) -> bool:
    return len("/".join(sighting.path)) <= RELATIVE_PATH_MAX


def _record(
    workspace_id: UUID,
    config: _Config,
    sightings: list[_Sighting],
    unknown: list[dict[str, object]],
    failed_names: set[str],
    *,
    now: datetime,
    skipped: int = 0,
) -> _Recorded:
    """Write what was seen, each row on its own (one that does not fit never stops the
    round), at most `ROWS_MAX` waiting rows per workspace, and a row only when it changed."""
    result = _Recorded(skipped=skipped)
    ready: list[tuple[datetime, _Sighting]] = []
    prefix = config.folder_name + "/"
    with workspace_context(workspace_id, actor=SYSTEM):
        rows = {
            (row.relative_path, row.device, row.inode): row
            for row in ScanFile.objects.filter(relative_path__startswith=prefix)
        }
        waiting = sum(1 for row in rows.values() if row.state == ScanFile.State.SEEN)
        seen_keys: set[tuple[str, int, int]] = set()
        for sighting in sightings:
            if not _fits(sighting):
                result.skipped += 1
                continue
            current = sighting.fingerprint
            key = ("/".join(sighting.path), current.device, current.inode)
            seen_keys.add(key)
            row = rows.get(key)
            if row is None:
                if waiting >= ROWS_MAX:
                    result.skipped += 1
                    continue
                if _saved(
                    ScanFile(
                        workspace_id=workspace_id,
                        relative_path=key[0],
                        size=current.size,
                        mtime_ns=current.mtime_ns,
                        device=current.device,
                        inode=current.inode,
                        stable_since=now,
                        first_seen_at=now,
                    )
                ):
                    waiting += 1
                else:
                    result.skipped += 1
                continue
            previous = Fingerprint(row.device, row.inode, row.size, row.mtime_ns)
            if (
                row.state == ScanFile.State.IMPORTED
                and row.removed_at is None
                and previous == current
            ):
                # A letter purged since keeps its source where it is (never imported
                # again); a removal that failed is tried again after an hour.
                retry = row.reason != REMOVE_FAILED or row.updated_at < now - REMOVE_RETRY
                if row.document_id is not None and retry:
                    result.removable.append((row.id, ImportedFile.from_row(row)))
                continue
            changed = row.state != ScanFile.State.SEEN
            if changed:
                # Another file under an old row's path and inode: a new sighting.
                row.state = ScanFile.State.SEEN
                row.reason = ""
                row.document = None
                row.removed_at = None
                row.first_seen_at = now
            take, since = stable(
                previous,
                row.stable_since,
                current,
                now,
                stable_seconds=settings.BRIEVIE_SCAN_STABLE_SECONDS,
            )
            changed = changed or (row.size, row.mtime_ns, row.stable_since) != (
                current.size,
                current.mtime_ns,
                since,
            )
            row.size, row.mtime_ns, row.stable_since = current.size, current.mtime_ns, since
            if changed and not _saved(row):
                result.skipped += 1
                continue
            if sighting.state == ScanFolder.State.PAUSED:
                continue
            if take:
                ready.append((since, sighting))
            elif now - (row.first_seen_at or now) >= NEVER_SETTLED_AFTER:
                # Empty, or still changing after an hour: not a scan that is being written.
                result.unsettled.append(sighting)
        for key, row in rows.items():
            if (
                row.state == ScanFile.State.IMPORTED
                and row.removed_at is None
                and key not in seen_keys
            ):
                # Removed by hand (or the removal went through before a crash).
                row.removed_at = now
                row.save(update_fields=["removed_at", "updated_at"])
            elif row.state == ScanFile.State.SEEN and key not in seen_keys:
                # Taken away by a person, or no longer read: forget it.
                row.delete()
            elif (
                row.state == ScanFile.State.FAILED
                and row.relative_path.rsplit("/", 1)[-1] not in failed_names
            ):
                # Removed (or put back) from mislukt/ by hand.
                row.delete()
        ScanFolderSettings.objects.filter(workspace_id=workspace_id).update(
            unknown_folders=unknown, skipped_count=result.skipped, updated_at=now
        )
    ready.sort(key=lambda item: (item[0], item[1].path))
    result.ready = [sighting for _since, sighting in ready]
    return result


def _saved(row: ScanFile) -> bool:
    """Save one row in a savepoint: a value the database refuses skips that file only."""
    try:
        with transaction.atomic():
            row.save()
    except DatabaseError:
        return False
    return True


def _processing(workspace_id: UUID) -> int:
    with workspace_context(workspace_id, actor=SYSTEM):
        return Document.objects.filter(
            source=Document.Source.WATCH_FOLDER, status=Document.Status.PROCESSING
        ).count()


def _take(workspace_id: UUID, root_fd: int, sighting: _Sighting) -> bool:
    """Read, check and import one stable file; move it to mislukt/ when it is refused.
    True when the file was taken in or moved."""
    if sighting.state == ScanFolder.State.GONE:
        return _fail(workspace_id, root_fd, sighting, "person_gone")
    try:
        data = scan_files.read_candidate(
            root_fd,
            sighting.path,
            sighting.fingerprint,
            max_bytes=settings.BRIEVIE_MAX_UPLOAD_BYTES,
        )
    except ScanRefusedError as refusal:
        if refusal.code == "too_large":
            return _fail(workspace_id, root_fd, sighting, "file_too_large")
        if refusal.code in ("not_a_file", "linked"):
            # No right to read it, or a second name of another file: not the job's to open.
            return _fail(workspace_id, root_fd, sighting, "unreadable")
        return False
    try:
        checked = check_file(
            data, max_bytes=settings.BRIEVIE_MAX_UPLOAD_BYTES, max_pages=settings.BRIEVIE_MAX_PAGES
        )
        row_id, imported = _import(workspace_id, sighting, data, checked)
    except FileRefusedError as refusal:
        return _fail(workspace_id, root_fd, sighting, refusal.code)
    except PersonGoneError:
        return _fail(workspace_id, root_fd, sighting, "person_gone")
    except PermanentError:
        return _fail(workspace_id, root_fd, sighting, "unreadable")
    except (ApiError, DatabaseError, OSError) as error:
        # A full disk or a database that went away is not the file's fault: next round.
        logger.warning("Scan folder import postponed: %s", type(error).__name__)
        return False
    _verify_and_remove(workspace_id, root_fd, row_id, imported, data=data)
    return True


def _import(
    workspace_id: UUID, sighting: _Sighting, data: bytes, checked: CheckedFile
) -> tuple[UUID, ImportedFile]:
    """The letter and the file's row as `imported`, in one short transaction."""
    path = sighting.path
    with workspace_context(workspace_id, actor=SYSTEM):
        workspace = Workspace.objects.get(id=workspace_id)
        person = (
            Person.objects.filter(id=sighting.person_id).first()
            if sighting.person_id is not None
            else None
        )
        if sighting.person_id is not None and person is None:
            raise PersonGoneError
        document, created = create_document_from_file(
            workspace=workspace,
            data=data,
            metadata=FileMetadataIn(
                client_id=scan_client_id(
                    workspace_id, path, checked.sha256, sighting.fingerprint.mtime_ns
                )
            ),
            source=Document.Source.WATCH_FOLDER,
            scanned_by=None,
            original_name=path[-1],
            preset_addressee=person,
            checked=checked,
        )
        current = sighting.fingerprint
        row = ScanFile.objects.select_for_update().get(
            relative_path="/".join(path), device=current.device, inode=current.inode
        )
        row.state = ScanFile.State.IMPORTED
        row.document = document
        row.size, row.mtime_ns = current.size, current.mtime_ns
        row.save()
        if created:
            logger.info("Scan folder letter %s made", document.id)
        return row.id, ImportedFile.from_row(row)


def _verify_and_remove(
    workspace_id: UUID, root_fd: int, row_id: UUID, imported: ImportedFile, *, data: bytes | None
) -> None:
    """Remove the source only after the sealed original reads back as the same file."""
    with workspace_context(workspace_id, actor=SYSTEM):
        row = ScanFile.objects.filter(id=row_id).first()
        if row is None or row.document_id is None:
            return
        document_id = row.document_id
        expected = (
            Document.objects.filter(id=document_id)
            .values_list("original_sha256", flat=True)
            .first()
        )
        key = workspace_key(workspace_id)
    if not expected:
        return
    try:
        stored = crypto.open_sealed(
            key,
            default_storage().read(document_intake.original_path(workspace_id, document_id)),
            aad=document_intake.original_aad(workspace_id, document_id),
        )
    except (OSError, crypto.DecryptionError):
        logger.warning("Scan folder letter %s: the original does not read back", document_id)
        return
    if hashlib.sha256(stored).hexdigest() != expected or (
        data is not None and hashlib.sha256(data).hexdigest() != expected
    ):
        logger.warning("Scan folder letter %s: the original differs", document_id)
        return
    try:
        scan_files.remove_imported(root_fd, imported)
    except (ScanRefusedError, OSError) as error:
        # No right to remove on the share (the Scanmap page's check shows it), or the file
        # changed: tried again in an hour, not every minute.
        code = error.code if isinstance(error, ScanRefusedError) else type(error).__name__
        logger.warning("Scan folder letter %s: source not removed: %s", document_id, code)
        with workspace_context(workspace_id, actor=SYSTEM):
            ScanFile.objects.filter(id=row_id).update(
                reason=REMOVE_FAILED, updated_at=timezone.now()
            )
        return
    with workspace_context(workspace_id, actor=SYSTEM):
        ScanFile.objects.filter(id=row_id).update(
            removed_at=timezone.now(), updated_at=timezone.now()
        )


def _fail(workspace_id: UUID, root_fd: int, sighting: _Sighting, code: str) -> bool:
    """Move the file to mislukt/ with the reason, and record it at its new place."""
    reason = REASON_TEXTS.get(code, REASON_TEXTS["unreadable"])
    try:
        moved = scan_files.move_to_failed(root_fd, sighting.path, reason_text=str(reason))
    except ScanRefusedError as refusal:
        logger.warning("Scan folder file not moved: %s", refusal.code)
        return False
    except OSError as error:
        logger.warning("Scan folder file not moved: %s", type(error).__name__)
        return False
    current = sighting.fingerprint
    with workspace_context(workspace_id, actor=SYSTEM):
        ScanFile.objects.filter(
            relative_path="/".join(sighting.path), device=current.device, inode=current.inode
        ).delete()
        ScanFile.objects.update_or_create(
            workspace_id=workspace_id,
            relative_path="/".join(moved),
            device=current.device,
            inode=current.inode,
            defaults={
                "size": current.size,
                "mtime_ns": current.mtime_ns,
                "stable_since": timezone.now(),
                "state": ScanFile.State.FAILED,
                "reason": code,
                "document": None,
                "removed_at": None,
            },
        )
    logger.info("Scan folder file refused: %s", code)
    return True

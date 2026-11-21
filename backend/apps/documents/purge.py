"""The purge of the trash: the only code that deletes a letter for good.

It runs as the system (`workspace_context(workspace_id, actor=SYSTEM)`), as the other
jobs do: it must reach every letter of the workspace, private ones included, and their
reader rows (only the system writes those). Nothing here runs in a request.

- `mark_expired` takes the purge decision for letters trashed `TRASH_RETENTION` ago
  (`purge_started_at`, an audit row `document_purged` `{reason: "expired"}`). A marked
  letter is never restorable and is listed nowhere.
- `purge_marked` finishes marked letters, each in three steps: a short transaction reads
  its page paths (still marked, under the row lock); outside any transaction
  `page_files.remove_document_files` removes the files (the guard is inside it); a short
  transaction deletes its tasks and then the letter, whose cascade takes everything
  that hangs off it. Files go before rows: a crash in between leaves a marked, hidden
  row that the next run finishes. A refusal or an `OSError` logs the error type and
  the id only, and keeps the row marked. One run tries each letter once, so letters
  that keep being refused never hold up the others.
- `purge_all` (hourly) and `purge_workspace` (after an admin's purge or empty) hold the
  back-up lock, so a back-up or restore never sees half a purge; a busy lock skips the
  run (the next hourly run finishes it). An unexpected error in one workspace is
  logged (its type and the workspace id) and the run goes on with the next.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from django.db import connection, transaction

from apps.audit.services import AuditActor
from apps.backup.errors import BackupError
from apps.backup.locks import backup_lock
from apps.documents import trash
from apps.documents.models import Document, DocumentPage
from apps.documents.page_files import PageFileRefused, remove_document_files
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace

logger = logging.getLogger(__name__)

PURGE_BATCH = 100


@dataclass(frozen=True)
class PurgeResult:
    purged: int
    refused: int
    # The letters this call took up, purged or not (not part of the comparison).
    tried: frozenset[UUID] = field(default=frozenset(), compare=False)


def mark_expired(workspace_id: UUID, *, now: datetime) -> int:
    """Mark the workspace's letters trashed at or before `now - TRASH_RETENTION` for the
    purge (the retention hook may keep one in the trash); returns how many."""
    with workspace_context(workspace_id, actor=SYSTEM):
        documents = (
            Document.objects.select_for_update(no_key=True)
            .filter(
                workspace_id=workspace_id,
                trashed_at__lte=now - trash.TRASH_RETENTION,
                purge_started_at__isnull=True,
            )
            .order_by("trashed_at", "id")
        )
        return sum(
            1
            for document in documents
            if trash.mark_for_purge(document, actor=AuditActor.system(), reason="expired")
        )


def _marked_ids(workspace_id: UUID, limit: int, skip: Collection[UUID]) -> list[UUID]:
    with workspace_context(workspace_id, actor=SYSTEM):
        return list(
            Document.objects.filter(workspace_id=workspace_id, purge_started_at__isnull=False)
            .exclude(id__in=skip)
            .order_by("purge_started_at", "id")
            .values_list("id", flat=True)[:limit]
        )


def _page_paths(workspace_id: UUID, document_id: UUID) -> list[str] | None:
    """Step 1: the letter's stored page paths, while it is still marked; None when it
    is gone or no longer marked."""
    with workspace_context(workspace_id, actor=SYSTEM):
        marked = (
            Document.objects.select_for_update(no_key=True)
            .filter(id=document_id, workspace_id=workspace_id, purge_started_at__isnull=False)
            .exists()
        )
        if not marked:
            return None
        return list(
            DocumentPage.objects.filter(document_id=document_id)
            .order_by("index")
            .values_list("storage_path", flat=True)
        )


def _delete_rows(workspace_id: UUID, document_id: UUID) -> bool:
    """Step 3: the letter's tasks (their key is SET_NULL, they would outlive it), then the
    letter; the cascade takes pages, extractions, links, events, suggestions, shares,
    reader rows, read marks and rule runs. False when it is gone or no longer marked."""
    with workspace_context(workspace_id, actor=SYSTEM), transaction.atomic():
        # No key, as every document lock (tests/test_privacy_locks.py); the DELETE below
        # takes the full row lock itself, in this transaction.
        document = (
            Document.objects.select_for_update(no_key=True)
            .filter(id=document_id, workspace_id=workspace_id, purge_started_at__isnull=False)
            .first()
        )
        if document is None:
            return False
        Task.objects.filter(document_id=document_id).delete()
        document.delete()
    return True


def purge_marked(
    workspace_id: UUID, *, limit: int = PURGE_BATCH, skip: Collection[UUID] = ()
) -> PurgeResult:
    """Remove the files and then the rows of up to `limit` marked letters of the
    workspace, leaving out `skip`. Outside a transaction only: rolled back, it would
    leave a restorable letter whose files are gone."""
    if connection.in_atomic_block:
        raise RuntimeError("The purge must run outside a transaction.")
    purged = refused = 0
    tried = _marked_ids(workspace_id, limit, skip)
    for document_id in tried:
        paths = _page_paths(workspace_id, document_id)
        if paths is None:
            continue
        try:
            remove_document_files(workspace_id, document_id, paths)
        except (PageFileRefused, OSError) as error:
            # Ids and the error type only: never a path, never anything of the letter.
            logger.error(
                "The purge of letter %s stopped before its rows: %s",
                document_id,
                type(error).__name__,
            )
            refused += 1
            continue
        if _delete_rows(workspace_id, document_id):
            purged += 1
    return PurgeResult(purged=purged, refused=refused, tried=frozenset(tried))


def _purge_until_done(workspace_id: UUID) -> None:
    """Batches until every marked letter was tried once in this run."""
    tried: set[UUID] = set()
    while True:
        result = purge_marked(workspace_id, limit=PURGE_BATCH, skip=tried)
        if not result.tried:
            return
        tried |= result.tried


def purge_workspace(workspace_id: UUID) -> None:
    """Purge the workspace's marked letters under the back-up lock; skip when it is busy."""
    try:
        with backup_lock():
            _purge_until_done(workspace_id)
    except BackupError as error:
        if error.code != "busy":
            raise
        logger.info("Trash purge skipped: a back-up or restore is running.")


def _purge_one_workspace(workspace_id: UUID, *, now: datetime) -> None:
    """Mark and purge one workspace; an unexpected error stops only this workspace."""
    try:
        mark_expired(workspace_id, now=now)
        _purge_until_done(workspace_id)
    except Exception as error:  # noqa: BLE001 - one workspace must not stop the others
        # The type and the id only: a database message can quote a letter's values.
        logger.error(
            "The trash purge of workspace %s stopped: %s", workspace_id, type(error).__name__
        )


def purge_all(*, now: datetime) -> None:
    """The hourly run: for every workspace, mark what expired and purge what is marked,
    under the back-up lock; skip the run when a back-up or restore holds it."""
    try:
        with backup_lock():
            for workspace_id in Workspace.objects.order_by("id").values_list("id", flat=True):
                _purge_one_workspace(workspace_id, now=now)
    except BackupError as error:
        if error.code != "busy":
            raise
        logger.info("Trash purge skipped: a back-up or restore is running.")

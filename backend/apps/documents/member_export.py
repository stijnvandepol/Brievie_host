"""A member's own export of their post (D 6.3, Beheer 5, part A), and an admin's export of
everything they may read ("Alles exporteren", archive spec S 9, AR13).

Asked for over the web, built by a job into a sealed file, downloadable only by the
member who asked for it, for 24 hours. What is in it comes from `member_document_ids`
(scope `mine`) or `admin_document_ids` (scope `all`), the two places that say which
documents an export holds; both read as the member, so the policies leave out every
letter they may not read. Every read goes through `export_context` (see
`apps.documents.export`); nothing here enters a workspace's context itself. `mine` obeys
`allow_member_export`; `all` does not (the admin who sets that switch may export what they
read), but needs an admin, re-read under the lock when asked for and again when built and
downloaded.
"""

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from functools import partial
from typing import BinaryIO
from uuid import UUID, uuid4

from django.conf import settings
from django.db import IntegrityError, connection, models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.codes import AuditCode, TargetKind
from apps.audit.services import AuditActor, AuditTarget, record
from apps.common.errors import ApiError
from apps.common.sealed_stream import SealedWriter, iter_opened
from apps.documents.export import estimate_bytes, export_context, write_export
from apps.documents.export_files import (
    UnsafePathError,
    export_file_path,
    free_bytes,
    list_export_files,
    open_export_for_read,
    open_export_for_write,
    remove_export_file,
)
from apps.documents.models import (
    LIVE,
    Document,
    DocumentAddressee,
    DocumentReader,
    MemberExport,
)
from apps.workspaces.keys import workspace_key
from apps.workspaces.locks import lock_own_role
from apps.workspaces.models import Membership, Workspace

logger = logging.getLogger(__name__)

# The reader rows that put a private letter in the member's own export.
_OWN_READER_REASONS = (
    DocumentReader.Reason.ADDRESSEE,
    DocumentReader.Reason.DEPARTMENT,
    DocumentReader.Reason.SHARE,
    # "Beheerd door": the managed person's private post is the manager's own post.
    DocumentReader.Reason.MANAGED,
)
# How long a finished export can be downloaded.
VALIDITY = timedelta(hours=24)
# A pending export older than this has lost its job.
PENDING_TIMEOUT = timedelta(hours=2)
# A failed export stays visible this long (so the member sees it), then goes.
FAILED_KEPT = timedelta(hours=24)
# A file without a row older than this is an orphan.
FILE_ORPHAN_AFTER = timedelta(hours=24)


def _not_found() -> ApiError:
    return ApiError(404, "export_not_found", _("We kunnen deze export niet vinden."))


def _stream_aad(workspace_id: UUID, export_id: UUID) -> bytes:
    """Binds the sealed stream to its workspace and export: it opens nowhere else."""
    return f"export/{workspace_id}/{export_id}".encode()


def member_document_ids(workspace_id: UUID, user_id: UUID) -> list[UUID]:
    """The documents of the member's own export, newest first: the one place that decides
    (S 4.18, PB decision 26).

    Read as the member, so the policies leave out every letter they cannot read: the
    letters on a person linked to the member, plus the private letters the member reads
    through a share, addressee or department reader row that has not expired. Never
    "every letter the member sees": the switches ("Iedereen ziet alle post", an admin
    who reads all, sorting) and departments' open post do not widen it, and emergency
    access is no reader row of the member's own post."""
    with export_context(workspace_id, reader_id=user_id):
        on_their_name = DocumentAddressee.objects.filter(
            document=models.OuterRef("pk"), person__user_id=user_id
        )
        private_read = DocumentReader.objects.filter(
            document=models.OuterRef("pk"), user_id=user_id, reason__in=_OWN_READER_REASONS
        ).filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now()))
        return list(
            Document.objects.filter(workspace_id=workspace_id)
            .filter(LIVE)
            .filter(
                models.Exists(on_their_name)
                | (
                    models.Q(visibility=Document.Visibility.RESTRICTED)
                    & models.Exists(private_read)
                )
            )
            .order_by("-created_at", "-id")
            .values_list("id", flat=True)
        )


def admin_document_ids(workspace_id: UUID, user_id: UUID) -> list[UUID]:
    """The documents of an admin's "Alles exporteren", newest first: every live document
    the admin may read, read as the admin (the policies leave out every private letter
    they may not read, whatever the switches). A letter the admin reads through emergency
    access is left out: an hour of reading is not a copy to keep."""
    with export_context(workspace_id, reader_id=user_id):
        emergency = DocumentReader.objects.filter(
            document=models.OuterRef("pk"),
            user_id=user_id,
            reason=DocumentReader.Reason.EMERGENCY,
        )
        return list(
            Document.objects.filter(workspace_id=workspace_id)
            .filter(LIVE)
            .exclude(models.Exists(emergency))
            .order_by("-created_at", "-id")
            .values_list("id", flat=True)
        )


def _document_ids(workspace_id: UUID, user_id: UUID, scope: str) -> list[UUID]:
    if scope == MemberExport.Scope.ALL:
        return admin_document_ids(workspace_id, user_id)
    return member_document_ids(workspace_id, user_id)


def _remove_files(storage_paths: list[str]) -> None:
    """Remove the files of these rows, one by one; a path that is not an export path is
    skipped (and the row's other files still go)."""
    for storage_path in storage_paths:
        try:
            remove_export_file(export_file_path(storage_path))
        except UnsafePathError:
            logger.error("An export has a stored path that is not an export file; kept.")
        except OSError as exc:
            logger.error("An export file could not be removed: %s", type(exc).__name__)


def _drop(exports: models.QuerySet[MemberExport]) -> None:
    """Delete these rows now and their files once the transaction has committed."""
    paths = list(exports.values_list("storage_path", flat=True))
    exports.delete()
    transaction.on_commit(partial(_remove_files, paths))


def create_my_export(*, membership: Membership, actor: AuditActor, scope: str) -> MemberExport:
    """Ask for an export of the member's post (`mine`) or, for an admin, of everything they
    may read (`all`); a job builds it.

    422 for another scope; `mine`: 403 `export_not_allowed` when the workspace switched
    exports off; `all`: 403 `not_admin` unless the caller is an admin (re-read under the
    lock of their membership), and 409 `export_running_elsewhere` while another admin's
    `all` export of the workspace is being built (one at a time per workspace). 409
    `export_too_large` when it would exceed `BRIEVIE_EXPORT_MAX_BYTES`. 409
    `export_running` while an earlier one is being built;
    an older finished (or failed) one is replaced, its file removed after the commit. One
    export per member, whatever its scope. Needs the workspace's RLS context (see
    workspaces.deps). The `export_created` row (count and scope) is in the same transaction.
    """
    workspace = membership.workspace
    if scope not in MemberExport.Scope.values:
        raise ApiError(422, "validation_error", _("Kies mijn archief of alles."))
    if scope == MemberExport.Scope.MINE and not workspace.allow_member_export:
        raise _not_allowed()
    from apps.documents.jobs import build_member_export  # the job module imports this one

    with transaction.atomic():
        role = lock_own_role(workspace.id, membership.user_id)
        if scope == MemberExport.Scope.ALL and role != Membership.Role.ADMIN:
            raise _not_admin()
        if scope == MemberExport.Scope.ALL:
            # One "Alles exporteren" at a time per workspace: every one holds about the whole
            # workspace twice (security review of PR #54, point 2).
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    [f"export-all-{workspace.id}"],
                )
            if (
                MemberExport.objects.filter(
                    workspace=workspace,
                    scope=MemberExport.Scope.ALL,
                    status=MemberExport.Status.PENDING,
                )
                .exclude(user_id=membership.user_id)
                .exists()
            ):
                raise ApiError(
                    409,
                    "export_running_elsewhere",
                    _("Er wordt al een export van alles gemaakt. Probeer het later nog eens."),
                )
        document_ids = _document_ids(workspace.id, membership.user_id, scope)
        count = len(document_ids)
        if estimate_bytes(workspace.id, membership.user_id, document_ids) > (
            settings.BRIEVIE_EXPORT_MAX_BYTES
        ):
            raise ApiError(409, "export_too_large", _too_large_message())
        mine = MemberExport.objects.select_for_update().filter(
            workspace=workspace, user_id=membership.user_id
        )
        earlier = mine.first()
        if earlier is not None and earlier.status == MemberExport.Status.PENDING:
            raise _running()
        if earlier is not None:
            _drop(MemberExport.objects.filter(id=earlier.id))
        export_id = uuid4()
        try:
            with transaction.atomic():
                export = MemberExport.objects.create(
                    id=export_id,
                    workspace=workspace,
                    user_id=membership.user_id,
                    scope=scope,
                    storage_path=f"exports/{workspace.id}/{export_id}.sealed",
                )
        except IntegrityError:
            # Another request of the same member got in first.
            raise _running() from None
        record(
            AuditCode.EXPORT_CREATED,
            actor=actor,
            target=AuditTarget(TargetKind.EXPORT, export.id),
            workspace_id=workspace.id,
            values={"document_count": count, "scope": scope},
        )
        build_member_export.defer(workspace_id=str(workspace.id), export_id=str(export.id))
    return export


def _too_large_message() -> str:
    return _("Deze export wordt groter dan deze server toestaat. Vraag de serverbeheerder.")


def has_room(estimate: int) -> bool:
    """Whether the export directory's disk holds `estimate` bytes with the reserve to spare."""
    return free_bytes() >= estimate + settings.BRIEVIE_EXPORT_FREE_RESERVE_BYTES


def _not_allowed() -> ApiError:
    return ApiError(
        403,
        "export_not_allowed",
        _("In deze werkruimte staat exporteren uit. Vraag de beheerder."),
    )


def _not_admin() -> ApiError:
    return ApiError(403, "not_admin", _("Alleen een beheerder kan alles exporteren."))


def _running() -> ApiError:
    return ApiError(
        409,
        "export_running",
        _("Je export wordt nog gemaakt. Probeer het zo nog eens."),
    )


def my_export(membership: Membership) -> MemberExport | None:
    """The member's own export, none when it ran out; never somebody else's."""
    export = MemberExport.objects.filter(
        workspace_id=membership.workspace_id, user_id=membership.user_id
    ).first()
    if export is None or _expired(export):
        return None
    return export


def _expired(export: MemberExport) -> bool:
    return export.status == MemberExport.Status.READY and (
        export.expires_at is None or export.expires_at <= timezone.now()
    )


def open_my_export(*, membership: Membership, actor: AuditActor) -> Iterator[bytes]:
    """The member's finished export as a stream of ZIP bytes.

    A plain function, not a generator: everything that needs the request happens now,
    inside it (its transaction and RLS context still open): the row (ready, the member's
    own, not expired, else 404 `export_not_found`; 403 `export_not_allowed` when exports
    were switched off since), the workspace key, the open file and the `export_downloaded`
    row. The returned iterator reads the file and needs neither the database nor the
    context, so the response can stream after the view has returned.
    """
    workspace = membership.workspace
    export = MemberExport.objects.filter(
        workspace=workspace,
        user_id=membership.user_id,
        status=MemberExport.Status.READY,
        expires_at__gt=timezone.now(),
    ).first()
    if export is None:
        raise _not_found()
    if export.scope == MemberExport.Scope.ALL:
        # Only while they are an admin; a former admin's copy is nobody's.
        if lock_own_role(workspace.id, membership.user_id) != Membership.Role.ADMIN:
            raise _not_found()
    elif not workspace.allow_member_export:
        raise _not_allowed()
    key = workspace_key(workspace.id)
    try:
        handle = open_export_for_read(export_file_path(export.storage_path))
    except (FileNotFoundError, UnsafePathError):
        raise _not_found() from None
    record(
        AuditCode.EXPORT_DOWNLOADED,
        actor=actor,
        target=AuditTarget(TargetKind.EXPORT, export.id),
        workspace_id=workspace.id,
    )
    return _plain_stream(handle, key, _stream_aad(workspace.id, export.id))


def _plain_stream(handle: BinaryIO, key: bytes, aad: bytes) -> Iterator[bytes]:
    try:
        yield from iter_opened(handle, key, aad=aad)
    finally:
        handle.close()


def remove_member_exports(workspace_id: UUID, user_id: UUID) -> None:
    """The member's export rows now, their files once the transaction has committed.
    Needs the workspace's RLS context (the caller removes the member inside it)."""
    _drop(MemberExport.objects.filter(workspace_id=workspace_id, user_id=user_id))


# --- the job's side -------------------------------------------------------------------


def build_export(workspace_id: UUID, export_id: UUID) -> None:
    """Build a pending export: the ZIP of the member's post, sealed, into its file.

    Short transactions only: the ids and the key first, each document inside
    `write_export`, the result last; no transaction is open while the file is written.
    Any failure marks the export failed and removes the partial file; the log gets the
    type of the exception, never its message (it may quote a letter).
    """
    with export_context(workspace_id, reader_id=None):
        export = (
            MemberExport.objects.select_related("user")
            .filter(workspace_id=workspace_id, id=export_id, status=MemberExport.Status.PENDING)
            .first()
        )
        if export is None:
            return
        user_id, member_name, storage_path = export.user_id, export.user.name, export.storage_path
        scope = export.scope
        role = (
            Membership.objects.filter(workspace_id=workspace_id, user_id=user_id)
            .values_list("role", flat=True)
            .first()
        )
        if role is None or (scope == MemberExport.Scope.ALL and role != Membership.Role.ADMIN):
            # The member left after asking (their export row normally goes with them):
            # nothing is read or written for someone who is no member.
            export.status = MemberExport.Status.FAILED
            export.save(update_fields=["status", "updated_at"])
            return
        key = workspace_key(workspace_id)
    try:
        path = export_file_path(storage_path)
        # A file from a job that died halfway.
        remove_export_file(path)
        document_ids = _document_ids(workspace_id, user_id, scope)
        # Measured before a byte is written (security review of PR #54, point 2): too large
        # for the server's limit, or for the room on its disk, is a failed export with a
        # reason the member sees, not a full disk.
        estimate = estimate_bytes(workspace_id, user_id, document_ids)
        refusal = (
            "too_large"
            if estimate > settings.BRIEVIE_EXPORT_MAX_BYTES
            else ("" if has_room(estimate) else "not_enough_space")
        )
        if refusal:
            logger.warning("Export %s refused: %s", export_id, refusal)
            _finish(workspace_id, export_id, MemberExport.Status.FAILED, failure_code=refusal)
            return
        title = (
            _("Brievie: alles wat %(name)s mag lezen") % {"name": member_name}
            if scope == MemberExport.Scope.ALL
            else _("Brievie: de post van %(name)s") % {"name": member_name}
        )
        with open_export_for_write(path) as handle:
            with SealedWriter(handle, key, aad=_stream_aad(workspace_id, export_id)) as sealed:
                counts = write_export(
                    workspace_id=workspace_id,
                    reader_id=user_id,
                    document_ids=document_ids,
                    target=sealed,
                    title=title,
                )
            handle.flush()
            size = handle.tell()
    except Exception as exc:  # noqa: BLE001 - whatever fails, the member sees a failed export
        logger.error("Export %s failed: %s", export_id, type(exc).__name__)
        _remove_files([storage_path])
        _finish(workspace_id, export_id, MemberExport.Status.FAILED)
        return
    if not _finish(
        workspace_id,
        export_id,
        MemberExport.Status.READY,
        document_count=counts.documents,
        size=size,
    ):
        # The export was removed (the member left) while it was built.
        _remove_files([storage_path])


def _finish(workspace_id: UUID, export_id: UUID, status: str, **fields: int | str) -> bool:
    """Move a still pending export to `status`; False when it is gone or not pending."""
    now = timezone.now()
    expires_at = now + VALIDITY if status == MemberExport.Status.READY else None
    with export_context(workspace_id, reader_id=None):
        changed = MemberExport.objects.filter(
            workspace_id=workspace_id, id=export_id, status=MemberExport.Status.PENDING
        ).update(status=status, expires_at=expires_at, updated_at=now, **fields)
    return changed == 1


def remove_expired() -> int:
    """Per workspace: finished exports past their 24 hours, failed ones past a day, and
    pending ones whose job died (over two hours old) lose their file, then their row (a
    pending one becomes failed, so the member sees it). Returns how many were handled."""
    moment = timezone.now()
    handled = 0
    for workspace_id in Workspace.objects.values_list("id", flat=True):
        handled += _sweep_workspace(workspace_id, moment)
    return handled + _remove_orphans(moment)


def _sweep_workspace(workspace_id: UUID, moment: datetime) -> int:
    due = (
        models.Q(status=MemberExport.Status.READY, expires_at__lte=moment)
        | models.Q(status=MemberExport.Status.PENDING, created_at__lte=moment - PENDING_TIMEOUT)
        | models.Q(status=MemberExport.Status.FAILED, updated_at__lte=moment - FAILED_KEPT)
    )
    with export_context(workspace_id, reader_id=None):
        found = list(
            MemberExport.objects.filter(workspace_id=workspace_id)
            .filter(due)
            .values_list("id", "status", "storage_path")
        )
    if not found:
        return 0
    # Files first: a crash leaves a row without a file (the next run removes the row), not
    # a file nobody knows about.
    _remove_files([path for _id, _status, path in found])
    with export_context(workspace_id, reader_id=None):
        for export_id, status, _path in found:
            rows = MemberExport.objects.filter(
                workspace_id=workspace_id, id=export_id, status=status
            ).filter(due)
            if status == MemberExport.Status.PENDING:
                rows.update(status=MemberExport.Status.FAILED, updated_at=moment)
            else:
                rows.delete()
    return len(found)


def _remove_orphans(moment: datetime) -> int:
    """Remove export files that have no row and are over a day old (a crash between a
    row's deletion and its file's removal leaves one). Only files named exactly
    `exports/<uuid>/<uuid>.sealed`, found two levels down and never through a link; each
    goes through the same guard as every removal. A young file may be a build that has
    not been recorded yet and is kept."""
    old = [f for f in list_export_files() if f.modified <= (moment - FILE_ORPHAN_AFTER).timestamp()]
    removed = 0
    for workspace_id in {f.workspace_id for f in old}:
        candidates = [f for f in old if f.workspace_id == workspace_id]
        with export_context(workspace_id, reader_id=None):
            known = set(
                MemberExport.objects.filter(
                    workspace_id=workspace_id, id__in=[f.export_id for f in candidates]
                ).values_list("id", flat=True)
            )
        for file in candidates:
            if file.export_id in known:
                continue
            try:
                remove_export_file(file.path)
                removed += 1
            except UnsafePathError:
                logger.error("An export file was refused by the guard; kept.")
            except OSError as exc:
                logger.error("An export file could not be removed: %s", type(exc).__name__)
    return removed

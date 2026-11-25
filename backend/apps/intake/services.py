"""A file from a browser (or later the scan folder) becomes a `processing` document
(intake spec S 1). The request checks the file and stores the original sealed; the worker
makes the pages (`apps.intake.prepare`)."""

import hashlib
import shutil
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.models import User
from apps.audit.services import AuditActor
from apps.common import crypto
from apps.common.errors import ApiError
from apps.documents import intake, own, reading
from apps.documents import services as document_services
from apps.documents.models import Document
from apps.documents.schemas import FileMetadataIn
from apps.documents.storage import DocumentStorage, default_storage
from apps.intake.files import FileKind, check_file, download_name, safe_original_name
from apps.pipeline.jobs import defer_processing
from apps.workspaces.context import system_actor
from apps.workspaces.keys import workspace_key
from apps.workspaces.models import Membership, Workspace


def create_document_from_file(
    *,
    workspace: Workspace,
    data: bytes,
    metadata: FileMetadataIn,
    source: Document.Source,
    scanned_by: User | None,
    original_name: str,
    actor: AuditActor | None = None,
    storage: DocumentStorage | None = None,
) -> tuple[Document, bool]:
    """Store `data` as a new document; `True` when it is new.

    The file is checked first (`FileRefusedError`, nothing stored). The same `client_id`
    with the same file answers the existing document (a retry after a lost answer);
    another file under that id is 409, as `createDocument`. With `kind = document` it is
    an own document, checked and organised as `create_document_from_upload` does. Needs
    the workspace's RLS context.
    """
    checked = check_file(
        data, max_bytes=settings.BRIEVIE_MAX_UPLOAD_BYTES, max_pages=settings.BRIEVIE_MAX_PAGES
    )
    own_data = own.validate_fields(metadata, today=timezone.localdate())
    if own_data is not None and scanned_by is None:
        # Only a member adds an own document; the scan folder makes letters.
        raise ValueError("an own document needs its uploader")

    existing = intake._find_existing(workspace, metadata.client_id)
    if existing is not None:
        return _same_file(existing, checked.sha256), False
    if free_bytes() < checked.size + settings.BRIEVIE_UPLOAD_FREE_RESERVE_BYTES:
        raise ApiError(
            507,
            "storage_full",
            _("Er is te weinig ruimte op de server om dit bestand te bewaren. Vraag de beheerder."),
        )

    storage = storage or default_storage()
    written: list[str] = []
    try:
        with transaction.atomic():
            if own_data is not None and scanned_by is not None:
                own.lock_creator(workspace, scanned_by)
                own.check_names(workspace, own_data)
            try:
                with transaction.atomic():
                    document = Document.objects.create(
                        workspace=workspace,
                        client_id=metadata.client_id,
                        scanned_by=scanned_by,
                        source=source,
                        page_count=0,
                        ocr_text="",
                        original_kind=checked.kind.value,
                        original_sha256=checked.sha256,
                        original_size=checked.size,
                        original_name=safe_original_name(original_name),
                        **(own.document_fields(own_data) if own_data is not None else {}),
                    )
            except IntegrityError:
                # Another request with this client_id got in first; nothing is written yet.
                with system_actor():
                    winner = Document.objects.filter(
                        workspace=workspace, client_id=metadata.client_id
                    ).first()
                if winner is None:
                    raise
                return _same_file(winner, checked.sha256), False
            path = intake.original_path(workspace.id, document.id)
            # Recorded first: a write that fails halfway must be cleaned up too.
            written.append(path)
            storage.write(
                path,
                crypto.seal(
                    workspace_key(workspace.id),
                    data,
                    aad=intake.original_aad(workspace.id, document.id),
                ),
            )
            if scanned_by is not None:
                if own_data is not None:
                    own.create_document(
                        document,
                        own_data,
                        user=scanned_by,
                        actor=actor if actor is not None else AuditActor.for_user(scanned_by),
                    )
                reading.mark_scanned(document, scanned_by)
            defer_processing(workspace_id=workspace.id, document_id=document.id)
    except BaseException:
        # The rows roll back; the sealed original has to go by hand.
        for written_path in written:
            storage.delete(written_path)
        raise
    return document, True


def free_bytes() -> int:
    """Free bytes on the disk of the storage root (of its nearest existing parent on a
    fresh install). Looks only."""
    target = Path(settings.BRIEVIE_STORAGE_ROOT)
    while not target.exists() and target.parent != target:
        target = target.parent
    return shutil.disk_usage(target).free


def _same_file(existing: Document, sha256: str) -> Document:
    return intake.existing_or_conflict(existing, same=existing.original_sha256 == sha256)


_CONTENT_TYPES = {
    Document.OriginalKind.PDF: "application/pdf",
    Document.OriginalKind.JPEG: "image/jpeg",
    Document.OriginalKind.PNG: "image/png",
}


def read_original(*, membership: Membership, document_id: UUID) -> tuple[bytes, str, str]:
    """The opened original of a document the member sees (through the visibility seam),
    its content type and a file name with the extension of its kind; 404
    `original_not_found` without one, `original_unreadable` when the file is gone or
    damaged."""
    document = document_services.get_document(membership=membership, document_id=document_id)
    kind = Document.OriginalKind(document.original_kind)
    if kind == Document.OriginalKind.NONE:
        raise ApiError(404, "original_not_found", _("Deze brief heeft geen origineel bestand."))
    workspace_id = membership.workspace_id
    try:
        data = crypto.open_sealed(
            workspace_key(workspace_id),
            default_storage().read(intake.original_path(workspace_id, document.id)),
            aad=intake.original_aad(workspace_id, document.id),
        )
    except (OSError, crypto.DecryptionError):
        data = None
    if data is None or hashlib.sha256(data).hexdigest() != document.original_sha256:
        # Gone from the disk or damaged: a clear answer, not a server error.
        raise ApiError(
            404,
            "original_unreadable",
            _("Het originele bestand van deze brief is niet te lezen."),
        )
    return data, _CONTENT_TYPES[kind], download_name(document.original_name, FileKind(kind.value))

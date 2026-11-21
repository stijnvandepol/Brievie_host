"""The export building block of Beheer 5: chosen documents as a streamed ZIP.

Part A uses it for a member's own export, part E adds the PDF per letter and the
workspace export. Three rules:

- Every read goes through `export_context`, the one place that decides which rows a
  reader may see (part B narrows it for private post). Nothing else in an export enters
  the workspace's context.
- `write_export` runs in short transactions, one per document, and never inside the
  caller's transaction or context: a page file is read from disk (and the ZIP written to
  its target) with no transaction open.
- Page files are sealed on disk. They are opened one at a time, in memory, and written
  into the ZIP; no unsealed file is written anywhere but the export's own output.
"""

import json
import logging
import time
import zipfile
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from io import RawIOBase
from typing import BinaryIO
from uuid import UUID

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.common import crypto
from apps.documents.intake import page_aad
from apps.documents.models import LIVE, Document, DocumentPage
from apps.documents.storage import default_storage
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.keys import workspace_key

logger = logging.getLogger(__name__)

EXPORT_VERSION = 1
DATA_FILE = "brievie-export.json"
README_FILE = "LEESMIJ.txt"


def export_context(workspace_id: UUID, *, reader_id: UUID | None) -> AbstractContextManager[None]:
    """The context in which an export reads: the workspace's, as seen by `reader_id`.

    A member's export passes the member: row-level security then leaves out every letter
    they may not read, whatever the caller picked. `reader_id=None` reads as the system
    and is only for the server-side bookkeeping of exports and the full copy of the
    workspace (`workspace_full_export`, not built yet): never for what one member gets.
    """
    return workspace_context(workspace_id, actor=reader_id if reader_id is not None else SYSTEM)


@dataclass(frozen=True)
class ExportCounts:
    documents: int
    pages: int


@dataclass(frozen=True)
class _PageRow:
    index: int
    storage_path: str


@dataclass(frozen=True)
class _DocumentRead:
    """What one document's short transaction yields."""

    entry: dict[str, object]
    pages: list[_PageRow]
    key: bytes


def write_export(
    *,
    workspace_id: UUID,
    reader_id: UUID | None,
    document_ids: Sequence[UUID],
    target: BinaryIO | RawIOBase,
    title: str,
) -> ExportCounts:
    """Write a ZIP of these documents to `target` (streamed: no seek, no temporary file).

    Holds `pagina's/<document id>/<n>.jpg` per page, `brievie-export.json` and
    `LEESMIJ.txt`. Opens its own short `export_context` per document, so call it with no
    transaction open. A document that is gone by the time it is read, or that
    the reader may not see, is skipped.
    """
    entries: list[dict[str, object]] = []
    page_total = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for document_id in dict.fromkeys(document_ids):
            read = _read_document(workspace_id, reader_id, document_id)
            if read is None:
                continue
            written = _write_pages(archive, workspace_id, reader_id, document_id, read)
            if written is None:
                continue
            page_total += len(written)
            read.entry["pages"] = written
            entries.append(read.entry)
        counts = ExportCounts(documents=len(entries), pages=page_total)
        data = {"version": EXPORT_VERSION, "documents": entries}
        _add(archive, DATA_FILE, json.dumps(data, ensure_ascii=False, indent=2).encode())
        _add(archive, README_FILE, _readme(title, counts).encode())
    return counts


def _read_document(
    workspace_id: UUID, reader_id: UUID | None, document_id: UUID
) -> _DocumentRead | None:
    """One document's data, its page rows and the workspace key, in one short transaction."""
    with export_context(workspace_id, reader_id=reader_id):
        document = (
            Document.objects.filter(LIVE).filter(workspace_id=workspace_id, id=document_id).first()
        )
        if document is None:
            return None
        pages = [
            _PageRow(index=p.index, storage_path=p.storage_path)
            for p in DocumentPage.objects.filter(document=document).order_by("index")
        ]
        entry: dict[str, object] = {
            "id": str(document.id),
            "status": document.status,
            "sender": document.sender,
            "document_type": document.document_type,
            "letter_date": _iso(document.letter_date),
            "summary": document.summary,
            "summary_by_ai": document.summary_by_ai,
            "created_at": document.created_at.isoformat(),
            "persons": sorted(
                document.addressee_links.values_list("person__display_name", flat=True)
            ),
            "labels": sorted(document.label_links.values_list("label__name", flat=True)),
            "tasks": [
                {
                    "type": task.type,
                    "description": task.description,
                    "deadline": _iso(task.deadline),
                    "status": task.status,
                }
                for task in document.tasks.order_by("deadline", "created_at", "id")
            ],
        }
        return _DocumentRead(entry=entry, pages=pages, key=workspace_key(workspace_id))


def _write_pages(
    archive: zipfile.ZipFile,
    workspace_id: UUID,
    reader_id: UUID | None,
    document_id: UUID,
    read: _DocumentRead,
) -> list[str] | None:
    """Open the document's page files one at a time into the ZIP; the page paths in it.

    No transaction is open here. None when the document vanished while its files were
    read (a file missing for a document that still exists is an error).
    """
    names: list[str] = []
    for page in read.pages:
        try:
            data = _open_page(workspace_id, document_id, page.index, page.storage_path, read.key)
        except FileNotFoundError:
            if _exists(workspace_id, reader_id, document_id):
                raise
            logger.info("A document was removed while it was exported; skipped.")
            return None
        name = f"pagina's/{document_id}/{page.index}.jpg"
        # JPEG data does not compress; storing it saves the time.
        _add(archive, name, data, compress=False)
        names.append(name)
    return names


def _open_page(
    workspace_id: UUID, document_id: UUID, index: int, storage_path: str, key: bytes
) -> bytes:
    sealed = default_storage().read(storage_path)
    return crypto.open_sealed(key, sealed, aad=page_aad(workspace_id, document_id, index))


def _exists(workspace_id: UUID, reader_id: UUID | None, document_id: UUID) -> bool:
    with export_context(workspace_id, reader_id=reader_id):
        return (
            Document.objects.filter(LIVE).filter(workspace_id=workspace_id, id=document_id).exists()
        )


def _add(archive: zipfile.ZipFile, name: str, data: bytes, *, compress: bool = True) -> None:
    info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def _iso(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def _readme(title: str, counts: ExportCounts) -> str:
    made = timezone.localtime().strftime("%d-%m-%Y %H:%M")
    return "\n".join(
        [
            title,
            _("Gemaakt op %(date)s met Brievie.") % {"date": made},
            "",
            _("Aantal brieven: %(documents)d") % {"documents": counts.documents},
            _("Aantal pagina's: %(pages)d") % {"pages": counts.pages},
            "",
            _("De map pagina's: elke brief heeft een eigen map met een foto per pagina."),
            _(
                "brievie-export.json: de gegevens van elke brief, zoals afzender, "
                "samenvatting, labels, personen en taken."
            ),
            "",
            _(
                "Let op: in de tekst en de gegevens is het BSN weggehaald. Op de foto's van "
                "de pagina's is het BSN wel te lezen."
            ),
            _(
                "Dit bestand is niet versleuteld zodra je het hebt gedownload. Bewaar het "
                "op een veilige plek en verwijder het als je het niet meer nodig hebt."
            ),
            "",
        ]
    )

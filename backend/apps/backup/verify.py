"""Check a restored installation against the manifest of its back-up (D 4.6 step 8).

Plain psycopg on a conninfo and a storage path given as arguments, no ORM, so the
same check runs against a second database and directory (the restore test of B2).
All reads are one read-only REPEATABLE READ transaction with row security off: a
role that is bound by row-level security fails instead of passing on zero rows.
Each problem is one Dutch sentence that names ids only, never a name or a path.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import UUID

import psycopg
from django.conf import settings
from django.utils.translation import gettext as _

from apps.backup.database import superuser_conninfo
from apps.backup.errors import BackupError
from apps.backup.manifest import Manifest, authority_fingerprint, manifest_from_json
from apps.backup.restore import parse_master_key
from apps.common import crypto
from apps.documents.intake import page_aad


@dataclass(frozen=True)
class VerifyReport:
    problems: list[str]
    workspaces: int
    pages_opened: int


@dataclass(frozen=True)
class _Page:
    workspace_id: UUID
    document_id: UUID
    index: int
    storage_path: str


@dataclass(frozen=True)
class _Restored:
    workspaces: list[UUID]
    documents: dict[UUID, int]
    pages: list[_Page]
    wrapped_keys: dict[UUID, bytes]


def _read(conninfo: str) -> _Restored:
    try:
        with psycopg.connect(conninfo) as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            conn.execute("SET row_security = off")
            workspaces = [
                row[0] for row in conn.execute("SELECT id FROM workspaces_workspace ORDER BY id")
            ]
            documents: dict[UUID, int] = dict(
                conn.execute(
                    "SELECT workspace_id, count(*) FROM documents_document GROUP BY workspace_id"
                ).fetchall()
            )
            pages = [
                _Page(workspace_id=row[0], document_id=row[1], index=row[2], storage_path=row[3])
                for row in conn.execute(
                    "SELECT workspace_id, document_id, index, storage_path"
                    " FROM documents_documentpage ORDER BY workspace_id, document_id, index"
                )
            ]
            wrapped_keys = {
                row[0]: bytes(row[1])
                for row in conn.execute(
                    "SELECT workspace_id, wrapped_key FROM workspaces_workspacekey"
                )
            }
    except psycopg.Error:
        # The driver's message can quote the connection string; say only the code.
        raise BackupError("restore_failed") from None
    return _Restored(workspaces, documents, pages, wrapped_keys)


def _page_file(storage_root: Path, stored: str) -> Path | None:
    """The page's file under the root; None for a stored path that leaves it."""
    path = PurePosixPath(stored)
    if path.is_absolute() or ".." in path.parts:
        return None
    return storage_root / path


def _counts(manifest: Manifest, restored: _Restored, pages: dict[UUID, list[_Page]]) -> list[str]:
    problems: list[str] = []
    present = set(restored.workspaces)
    for workspace_id in sorted(set(manifest.workspaces) - present):
        problems.append(_("Werkruimte %(id)s ontbreekt in de database.") % {"id": workspace_id})
    for workspace_id in sorted(present - set(manifest.workspaces)):
        problems.append(
            _("Werkruimte %(id)s staat niet in de inhoudsopgave van de back-up.")
            % {"id": workspace_id}
        )
    for workspace_id in sorted(present & set(manifest.workspaces)):
        expected = manifest.workspaces[workspace_id]
        documents = restored.documents.get(workspace_id, 0)
        if documents != expected.documents:
            problems.append(
                _("Werkruimte %(id)s heeft %(found)s documenten, de back-up had er %(expected)s.")
                % {"id": workspace_id, "found": documents, "expected": expected.documents}
            )
        found_pages = len(pages[workspace_id])
        if found_pages != expected.pages:
            problems.append(
                _("Werkruimte %(id)s heeft %(found)s pagina's, de back-up had er %(expected)s.")
                % {"id": workspace_id, "found": found_pages, "expected": expected.pages}
            )
    return problems


def _page_sentence(template: str, page: _Page) -> str:
    return template % {
        "index": page.index,
        "document": page.document_id,
        "workspace": page.workspace_id,
    }


def _open_one_page(
    workspace_id: UUID,
    pages: list[_Page],
    files: dict[_Page, Path],
    wrapped: bytes | None,
    master_key: bytes,
) -> str | None:
    """Open the workspace's first page whose file is there; a problem, or None."""
    page = next((page for page in pages if page in files), None)
    if page is None:
        return None  # no page, or every file missing (each already reported)
    if wrapped is None:
        return _("Werkruimte %(id)s heeft geen sleutel.") % {"id": workspace_id}
    try:
        key = crypto.open_sealed(master_key, wrapped, aad=workspace_id.bytes)
    except crypto.DecryptionError:
        return _("De sleutel van werkruimte %(id)s past niet bij de hoofdsleutel.") % {
            "id": workspace_id
        }
    try:
        data = files[page].read_bytes()
        crypto.open_sealed(key, data, aad=page_aad(workspace_id, page.document_id, page.index))
    except (OSError, crypto.DecryptionError):
        return _page_sentence(
            _(
                "Pagina %(index)s van document %(document)s in werkruimte %(workspace)s "
                "kon niet worden geopend."
            ),
            page,
        )
    return None


def _authority(manifest: Manifest, authority_dir: Path | None) -> list[str]:
    if manifest.authority_sha256 is None or authority_dir is None:
        return []
    found = authority_fingerprint(authority_dir)
    if found is None:
        return [_("Het certificaat van de server ontbreekt.")]
    if found != manifest.authority_sha256:
        return [_("Het certificaat van de server is niet hetzelfde als in de back-up.")]
    return []


def verify_restore(
    *,
    conninfo: str,
    storage_root: Path,
    manifest: Manifest,
    master_key: bytes,
    authority_dir: Path | None,
) -> VerifyReport:
    restored = _read(conninfo)
    pages: dict[UUID, list[_Page]] = defaultdict(list)
    for page in restored.pages:
        pages[page.workspace_id].append(page)
    problems = _counts(manifest, restored, pages)

    files: dict[_Page, Path] = {}
    for page in restored.pages:
        path = _page_file(storage_root, page.storage_path)
        if path is not None and path.is_file():
            files[page] = path
        else:
            problems.append(
                _page_sentence(
                    _(
                        "Pagina %(index)s van document %(document)s in werkruimte "
                        "%(workspace)s ontbreekt in de opslag."
                    ),
                    page,
                )
            )

    opened = 0
    for workspace_id in restored.workspaces:
        problem = _open_one_page(
            workspace_id,
            pages[workspace_id],
            files,
            restored.wrapped_keys.get(workspace_id),
            master_key,
        )
        if problem is not None:
            problems.append(problem)
        elif any(page in files for page in pages[workspace_id]):
            opened += 1

    problems += _authority(manifest, authority_dir)
    return VerifyReport(problems=problems, workspaces=len(restored.workspaces), pages_opened=opened)


def verify_installation(
    *, dbname: str, storage_root: Path, manifest_path: Path, authority_dir: Path | None
) -> VerifyReport:
    """`verify_restore` as the superuser, with the master key of this environment."""
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise BackupError("manifest_unreadable") from None
    manifest = manifest_from_json(manifest_text)
    master_key = parse_master_key(str(settings.BRIEVIE_MASTER_KEY))
    return verify_restore(
        conninfo=superuser_conninfo(dbname=dbname),
        storage_root=storage_root,
        manifest=manifest,
        master_key=master_key,
        authority_dir=authority_dir,
    )

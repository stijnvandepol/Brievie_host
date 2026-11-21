"""Check a restored installation against the manifest of its back-up (D 4.6 step 8).

Plain psycopg on a conninfo and a storage path given as arguments, no ORM, so the
same check runs against a second database and directory (the restore test of B2).
All reads are one read-only REPEATABLE READ transaction with row security off: a
role that is bound by row-level security fails instead of passing on zero rows.
Each problem is one Dutch sentence that names ids only, never a name or a path.
"""

import os
import stat
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
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
    privacy: list[str]


# The helper functions the visibility policies call (S 4.9.1); all six or the policies
# fail on every read.
VISIBILITY_FUNCTIONS = (
    "brievie_actor_id",
    "brievie_is_system",
    "brievie_sees_all_post",
    "brievie_my_post_ids",
    "brievie_covered_post_ids",
    "brievie_actor_sorts_post",
)
# The restrictive policies of the letter table and of every table with a letter key.
VISIBILITY_POLICIES = (
    "document_visibility",
    "document_visibility_update",
    "document_visibility_delete",
)
READER_TABLE = "documents_documentreader"
# The read marks: besides the letter's policies, a member reads only their own rows.
READ_MARK_TABLE = "documents_documentreadmark"
READ_MARK_POLICY = "read_mark_own"


def privacy_problems(conn: psycopg.Connection[tuple[object, ...]]) -> list[str]:
    """A restore without the visibility functions or policies must not pass: the counts
    alone would agree, and every member would then see every letter, or none (PB T21).
    Read in the caller's transaction. A back-up from before the functions existed
    (migration `workspaces.0007_visibility_functions`) is not checked for them. A problem
    names a table at most, never a person or a letter."""
    expected = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM django_migrations"
        " WHERE app = 'workspaces' AND name = '0007_visibility_functions')"
    ).fetchone()
    if expected is None or not expected[0]:
        return []
    problems: list[str] = []
    functions = conn.execute(
        "SELECT count(DISTINCT p.proname) FROM pg_catalog.pg_proc p"
        " JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'public' AND p.proname = ANY(%s)",
        [list(VISIBILITY_FUNCTIONS)],
    ).fetchone()
    if functions is None or functions[0] != len(VISIBILITY_FUNCTIONS):
        problems.append(_("De database mist de functies voor wie welke post ziet."))
    children = [
        str(row[0]).strip('"')
        for row in conn.execute(
            "SELECT DISTINCT conrelid::regclass::text FROM pg_catalog.pg_constraint"
            " WHERE contype = 'f' AND confrelid = 'public.documents_document'::regclass"
        )
    ]
    tables = ["documents_document", *sorted(t for t in children if t != READER_TABLE)]
    for table in tables:
        found = conn.execute(
            "SELECT count(*) FROM pg_catalog.pg_policy"
            " WHERE polrelid = %s::regclass AND polname = ANY(%s) AND NOT polpermissive",
            [f"public.{table}", list(VISIBILITY_POLICIES)],
        ).fetchone()
        if found is None or found[0] != len(VISIBILITY_POLICIES):
            problems.append(
                _("De database mist de regels voor wie welke post ziet in tabel %(table)s.")
                % {"table": table}
            )
    reader = conn.execute(
        "SELECT count(*) FROM pg_catalog.pg_policy"
        " WHERE polrelid = %s::regclass AND polname = 'reader_scope'",
        [f"public.{READER_TABLE}"],
    ).fetchone()
    if reader is None or reader[0] != 1:
        problems.append(
            _("De database mist de regels voor wie welke post ziet in tabel %(table)s.")
            % {"table": READER_TABLE}
        )
    # A back-up from before the read marks (documents/0022) has no such table.
    mark = conn.execute(
        "SELECT to_regclass(%s) IS NULL OR EXISTS (SELECT 1 FROM pg_catalog.pg_policy"
        " WHERE polrelid = to_regclass(%s) AND polname = %s AND NOT polpermissive)",
        [f"public.{READ_MARK_TABLE}", f"public.{READ_MARK_TABLE}", READ_MARK_POLICY],
    ).fetchone()
    if mark is None or not mark[0]:
        problems.append(
            _("De database mist de regels voor wie welke post ziet in tabel %(table)s.")
            % {"table": READ_MARK_TABLE}
        )
    return problems


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
            privacy = privacy_problems(conn)
    except psycopg.Error:
        # The driver's message can quote the connection string; say only the code.
        raise BackupError("restore_failed") from None
    return _Restored(workspaces, documents, pages, wrapped_keys, privacy)


def _page_file(storage_root: Path, stored: str) -> tuple[Path | None, str]:
    """The page's regular file inside the root, or None and the problem's sentence.

    A stored path is confined to the root after resolving links, and the file itself
    must be a regular file, not a link (a page outside the store would pass otherwise).
    """
    candidate = storage_root / stored  # an absolute stored path replaces the root
    try:
        inside = candidate.resolve().is_relative_to(storage_root.resolve())
    except (OSError, RuntimeError):  # a link loop
        inside = False
    if not inside:
        return None, _(
            "Pagina %(index)s van document %(document)s in werkruimte %(workspace)s "
            "ligt buiten de opslag."
        )
    try:
        info = os.lstat(candidate)
    except OSError:
        return None, _(
            "Pagina %(index)s van document %(document)s in werkruimte %(workspace)s "
            "ontbreekt in de opslag."
        )
    if not stat.S_ISREG(info.st_mode):
        return None, _(
            "Pagina %(index)s van document %(document)s in werkruimte %(workspace)s "
            "is geen gewoon bestand in de opslag."
        )
    return candidate, ""


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
    if manifest.authority_sha256 is None:
        return []
    if authority_dir is None:
        return [
            _(
                "Het certificaat van de server is niet gecontroleerd: geef de map met de "
                "certificaatautoriteit op."
            )
        ]
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
    problems = _counts(manifest, restored, pages) + restored.privacy

    files: dict[_Page, Path] = {}
    for page in restored.pages:
        path, why = _page_file(storage_root, page.storage_path)
        if path is not None:
            files[page] = path
        else:
            problems.append(_page_sentence(why, page))

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

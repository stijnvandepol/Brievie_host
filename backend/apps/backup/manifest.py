"""What a back-up records about itself; a restore verifies against it."""

import json
import re
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg
from django.conf import settings
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from apps.backup.errors import BackupError
from apps.devices.pki import certificate_sha256

MANIFEST_FORMAT = 1
MANIFEST_NAME = "manifest.json"
AUTHORITY_FILE = "root.crt"
# What a manifest may hold in the fields a restore prints as `key=value` lines.
_HOSTNAME = re.compile(r"[A-Za-z0-9._-]{1,253}")
_VERSION = re.compile(r"[\x21-\x7e]{1,64}")


@dataclass(frozen=True)
class WorkspaceCounts:
    documents: int
    pages: int
    page_files: int


@dataclass(frozen=True)
class Manifest:
    format: int
    run_id: UUID
    created_at: datetime
    brievie_version: str
    migrations: dict[str, list[str]]
    postgres_version: str
    hostname: str
    authority_sha256: str | None
    workspaces: dict[UUID, WorkspaceCounts]


def brievie_version() -> str:
    with (Path(settings.BASE_DIR) / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def host_name() -> str:
    return str(settings.BRIEVIE_HOSTNAME)


def authority_fingerprint(authority_dir: Path) -> str | None:
    path = authority_dir / AUTHORITY_FILE
    if not path.is_file():
        return None
    return certificate_sha256(path.read_text())


def _count_page_files(storage_root: Path, workspace_id: UUID) -> int:
    return sum(1 for path in (storage_root / str(workspace_id)).glob("*/*.page") if path.is_file())


def build_manifest(
    *, run_id: UUID, conninfo: str, storage_root: Path, authority_dir: Path
) -> Manifest:
    """Count as the back-up role with row security off.

    A role that cannot bypass row-level security fails here with an error instead
    of reading zero rows: a manifest that undercounts would make a restore pass.
    """
    with psycopg.connect(conninfo) as conn:
        # One snapshot, so documents and pages are counted at the same moment.
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        conn.execute("SET row_security = off")
        workspace_ids = [
            row[0] for row in conn.execute("SELECT id FROM workspaces_workspace ORDER BY id")
        ]
        documents: dict[UUID, int] = dict(
            conn.execute(
                "SELECT workspace_id, count(*) FROM documents_document GROUP BY workspace_id"
            ).fetchall()
        )
        pages: dict[UUID, int] = dict(
            conn.execute(
                "SELECT workspace_id, count(*) FROM documents_documentpage GROUP BY workspace_id"
            ).fetchall()
        )
        migrations: dict[str, list[str]] = {}
        for app, name in conn.execute("SELECT app, name FROM django_migrations ORDER BY app, name"):
            migrations.setdefault(app, []).append(name)
        version_row = conn.execute("SHOW server_version").fetchone()
    return Manifest(
        format=MANIFEST_FORMAT,
        run_id=run_id,
        created_at=timezone.now(),
        brievie_version=brievie_version(),
        migrations=migrations,
        postgres_version=str(version_row[0]) if version_row else "",
        hostname=host_name(),
        authority_sha256=authority_fingerprint(authority_dir),
        workspaces={
            workspace_id: WorkspaceCounts(
                documents=int(documents.get(workspace_id, 0)),
                pages=int(pages.get(workspace_id, 0)),
                page_files=_count_page_files(storage_root, workspace_id),
            )
            for workspace_id in workspace_ids
        },
    )


def manifest_to_json(m: Manifest) -> str:
    return json.dumps(
        {
            "format": m.format,
            "run_id": str(m.run_id),
            "created_at": m.created_at.isoformat(),
            "brievie_version": m.brievie_version,
            "migrations": m.migrations,
            "postgres_version": m.postgres_version,
            "hostname": m.hostname,
            "authority_sha256": m.authority_sha256,
            "workspaces": {
                str(workspace_id): {
                    "documents": counts.documents,
                    "pages": counts.pages,
                    "page_files": counts.page_files,
                }
                for workspace_id, counts in m.workspaces.items()
            },
        },
        indent=2,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at has no offset")
    return value


def _names(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(name, str) for name in value):
        raise ValueError("migrations are not a list of names")
    return list(value)


def _matching(value: object, pattern: re.Pattern[str]) -> str:
    """A text field that lands in `key=value` output: refuse anything but plain text."""
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError("unexpected characters")
    return value


def manifest_from_json(text: str) -> Manifest:
    try:
        data = json.loads(text)
        if type(data["format"]) is not int or data["format"] != MANIFEST_FORMAT:
            raise BackupError("manifest_unreadable")
        authority = data["authority_sha256"]
        return Manifest(
            format=MANIFEST_FORMAT,
            run_id=UUID(data["run_id"]),
            created_at=_aware(datetime.fromisoformat(data["created_at"])),
            brievie_version=_matching(data["brievie_version"], _VERSION),
            migrations={str(app): _names(names) for app, names in data["migrations"].items()},
            postgres_version=str(data["postgres_version"]),
            hostname=_matching(data["hostname"], _HOSTNAME),
            authority_sha256=None if authority is None else str(authority),
            workspaces={
                UUID(key): WorkspaceCounts(
                    documents=int(value["documents"]),
                    pages=int(value["pages"]),
                    page_files=int(value["page_files"]),
                )
                for key, value in data["workspaces"].items()
            },
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise BackupError("manifest_unreadable") from None


def unknown_migrations(m: Manifest) -> list[str]:
    """Migrations of the manifest this code does not have: a back-up of a newer schema.

    Compares names on disk, so it assumes no squashed migration whose replaced files
    were deleted.
    """
    known = MigrationLoader(None, ignore_no_migrations=True).disk_migrations
    return [
        f"{app}.{name}"
        for app, names in m.migrations.items()
        for name in names
        if (app, name) not in known
    ]

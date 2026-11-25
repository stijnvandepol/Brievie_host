"""Helpers for the document API tests: fake page images and multipart uploads."""

import base64
import hashlib
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.devices.services import create_pairing_code, pair_device
from apps.documents.models import Document, DuplicateCandidate
from apps.people.models import Person
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.visibility_support import as_actor

if TYPE_CHECKING:
    from tests.helpers import Response

# A whole 1 x 1 baseline JPEG (the same file as .github/scripts/restore-seed.py).
_BASE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////"
    "////////////////////////////////////////////////////////wgALCAAB"
    "AAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
)
# Its frame header (progressive), after the quantisation table.
_SOF_AT = _BASE_JPEG.index(b"\xff\xc2")


def jpeg(seed: str = "pagina", size: int = 2000) -> bytes:
    """A whole JPEG of about `size` bytes whose content depends on `seed`.

    The server checks a page's structure (apps.documents.jpeg) but never decodes it;
    comment segments carry the seed, so two seeds give two hashes.
    """
    filler = hashlib.sha256(seed.encode()).digest()
    room = max(len(filler), size - len(_BASE_JPEG))
    comment = (filler * (room // len(filler) + 1))[:room]
    segments = b"".join(
        b"\xff\xfe" + (len(part) + 2).to_bytes(2, "big") + part
        for part in (comment[i : i + 65000] for i in range(0, len(comment), 65000))
    )
    return _BASE_JPEG[:2] + segments + _BASE_JPEG[2:]


def jpeg_of_size(width: int, height: int) -> bytes:
    """`jpeg()` with another width and height in its frame header."""
    data = jpeg()
    at = len(data) - len(_BASE_JPEG) + _SOF_AT + 5
    return data[:at] + height.to_bytes(2, "big") + width.to_bytes(2, "big") + data[at + 4 :]


def admin_of(workspace: Workspace) -> User:
    return Membership.objects.get(workspace=workspace, role="admin").user


def bearer_for(user: User) -> dict[str, str]:
    _, code = create_pairing_code(user)
    _, token = pair_device(code=code, device_name="iPhone")
    return {"Authorization": f"Bearer {token}"}


def documents_url(workspace_id: object) -> str:
    return f"/api/v1/workspaces/{workspace_id}/documents"


def metadata_for(
    pages: list[bytes], texts: list[str] | None = None, client_id: uuid.UUID | None = None
) -> dict[str, object]:
    texts = texts if texts is not None else ["" for _ in pages]
    return {
        "client_id": str(client_id or uuid.uuid4()),
        "pages": [
            {"sha256": hashlib.sha256(page).hexdigest(), "text": text}
            for page, text in zip(pages, texts, strict=True)
        ],
    }


def upload(
    client: Client,
    workspace_id: object,
    pages: list[bytes],
    *,
    texts: list[str] | None = None,
    client_id: uuid.UUID | None = None,
    metadata: object = None,
    headers: dict[str, str] | None = None,
    content_type: str = "image/jpeg",
) -> "Response":
    body = metadata if metadata is not None else metadata_for(pages, texts, client_id)
    return client.post(
        documents_url(workspace_id),
        {
            "metadata": body if isinstance(body, str) else json.dumps(body),
            "page": [
                SimpleUploadedFile(f"{index}.jpg", page, content_type=content_type)
                for index, page in enumerate(pages, start=1)
            ],
        },
        headers=headers,
    )


def upload_file(
    client: Client,
    workspace_id: object,
    data: bytes,
    *,
    name: str = "brief.pdf",
    client_id: uuid.UUID | None = None,
) -> "Response":
    """A browser's `uploadDocumentFile`: one file, its kind read from the content."""
    return client.post(
        f"/api/v1/workspaces/{workspace_id}/documents/files",
        {
            "metadata": json.dumps({"client_id": str(client_id or uuid.uuid4())}),
            "file": SimpleUploadedFile(name, data, content_type="application/octet-stream"),
        },
    )


def make_document(workspace: Workspace, status: str = "done") -> Document:
    """A document row without pages, in `status`; enough for organising tests."""
    with workspace_context(workspace.id, actor=SYSTEM):
        return Document.objects.create(
            workspace=workspace, client_id=uuid.uuid4(), page_count=1, status=status
        )


def trashed(
    document: Document,
    *,
    by: User | None = None,
    days_ago: int = 0,
    purge_started: bool = False,
) -> Document:
    """Put the letter in the trash without the service (`days_ago` days ago, by `by`), and
    mark its purge as started when asked; written as the system. The fresh row."""
    now = timezone.now()
    with workspace_context(document.workspace_id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(
            trashed_at=now - timedelta(days=days_ago),
            trashed_by=by,
            purge_started_at=now if purge_started else None,
        )
        return Document.objects.get(id=document.id)


def make_person(workspace: Workspace, name: str, *, archived: bool = False) -> Person:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Person.objects.create(
            workspace=workspace,
            display_name=name,
            archived_at=timezone.now() if archived else None,
        )


def pair(first: Document, second: Document, *, state: str = "open") -> DuplicateCandidate:
    """A duplicate pair of two letters in `state`, ids ordered, written as the system."""
    low, high = sorted((first, second), key=lambda d: d.id)
    with as_actor(low.workspace, SYSTEM):
        return DuplicateCandidate.objects.create(
            workspace_id=low.workspace_id,
            first=low,
            second=high,
            state=state,
            text_similarity=Decimal("0.950"),
            number_overlap=Decimal("1.000"),
        )

"""Helpers for the document API tests: fake page images and multipart uploads."""

import hashlib
import json
import uuid
from typing import TYPE_CHECKING

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.accounts.models import User
from apps.devices.services import create_pairing_code, pair_device
from apps.workspaces.models import Membership, Workspace

if TYPE_CHECKING:
    from tests.helpers import Response

JPEG_MAGIC = b"\xff\xd8\xff\xe0"


def jpeg(seed: str = "pagina", size: int = 2000) -> bytes:
    """Bytes that start like a JPEG; the server stores them, it never decodes them."""
    filler = hashlib.sha256(seed.encode()).digest()
    return (JPEG_MAGIC + filler * (size // len(filler) + 1))[:size]


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

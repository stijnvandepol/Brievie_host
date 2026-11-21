"""Seeds a running Brievie server for the CI job `restore` (.github/workflows/ci.yml).

Run inside the api container, through the app's own services:

    docker compose ... exec -T api python manage.py shell -c "$(cat .github/scripts/restore-seed.py)"

It makes one user, two workspaces with that user as admin, one letter of one page in
each, two AI decisions per workspace (AI stays off: no call leaves the machine), a
pairing code and a paired device. The ONLY thing it prints is one JSON line:

    {"token": "...", "workspaces": [{"workspace": "<uuid>", "document": "<uuid>",
                                     "page_sha256": "<hex of the page as the API returns it>"}, ...]}

`token` is the device's bearer token: the job masks it and keeps it out of every process
argument. The password of the user is random and never printed or stored.
"""

import base64
import hashlib
import json
import secrets
import sys
import uuid

from apps.accounts.models import User
from apps.devices.services import create_pairing_code, pair_device
from apps.documents.intake import create_document_from_upload
from apps.documents.schemas import UploadMetadataIn
from apps.pipeline import settings_service
from apps.workspaces.context import workspace_context
from apps.workspaces.services import create_workspace

# A real, decodable 1x1 JPEG (134 bytes). Each page gets its own comment segment
# (marker FFFE) right after the start-of-image marker: still a valid JPEG, but no two
# pages are equal, so a page in the wrong place fails the hash check.
_BASE_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////"
    "////////////////////////////////////////////////////////wgALCAAB"
    "AAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA="
)
_LOCAL_ADDRESS = "http://localhost:11434/v1"


def make_page() -> bytes:
    comment = secrets.token_bytes(1500)
    length = (len(comment) + 2).to_bytes(2, "big")
    return _BASE_JPEG[:2] + b"\xff\xfe" + length + comment + _BASE_JPEG[2:]


def seed() -> dict[str, object]:
    user = User.objects.create_user(
        f"restore-seed-{uuid.uuid4().hex[:8]}@example.com",
        secrets.token_urlsafe(24),
        name="Restore seed",
    )
    actor = settings_service.Actor(user_id=user.id, label="restore seed")
    entries: list[dict[str, str]] = []
    for name, kind in (("Thuis", "home"), ("Zakelijk", "business")):
        workspace = create_workspace(name=name, kind=kind, admin=user)
        with workspace_context(workspace.id, actor=user.id):
            # Two decisions in the history (the own network, then off); neither makes
            # a call, and the workspace ends with AI off.
            for mode, address, model in (("local", _LOCAL_ADDRESS, "seed"), ("off", "", "")):
                settings_service.configure(
                    workspace_id=workspace.id,
                    mode=mode,
                    base_url=address,
                    model=model,
                    api_key=None,
                    actor=actor,
                )
        page = make_page()
        digest = hashlib.sha256(page).hexdigest()
        metadata = UploadMetadataIn.model_validate(
            {"client_id": str(uuid.uuid4()), "pages": [{"sha256": digest, "text": ""}]}
        )
        with workspace_context(workspace.id, actor=user.id):
            document, _created = create_document_from_upload(
                workspace=workspace, user=user, metadata=metadata, pages=[page]
            )
        entries.append(
            {"workspace": str(workspace.id), "document": str(document.id), "page_sha256": digest}
        )
    _pairing, code = create_pairing_code(user)
    _device, token = pair_device(code=code, device_name="Restore seed")
    return {"token": token, "workspaces": entries}


sys.stdout.write(json.dumps(seed(), separators=(",", ":")) + "\n")

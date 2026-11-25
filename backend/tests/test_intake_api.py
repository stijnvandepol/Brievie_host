"""Uploading a PDF or photo from a browser and downloading the original (intake plan
Task 6)."""

import json
import uuid
from pathlib import Path

from django.conf import settings as django_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from pytest_django import Settings

from apps.documents.models import Document
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, bearer_for, jpeg, upload, upload_file
from tests.factories import UserFactory
from tests.intake_fixtures import make_encrypted_pdf, make_pdf


def _url(workspace_id: object) -> str:
    return f"/api/v1/workspaces/{workspace_id}/documents/files"


def _send(
    client: Client,
    workspace_id: object,
    data: bytes,
    *,
    name: str = "brief.pdf",
    client_id: uuid.UUID | None = None,
    headers: dict[str, str] | None = None,
    **fields: object,
) -> object:
    metadata = {"client_id": str(client_id or uuid.uuid4()), **fields}
    return client.post(
        _url(workspace_id),
        {
            "metadata": json.dumps(metadata),
            # The browser's type says nothing: the content decides.
            "file": SimpleUploadedFile(name, data, content_type="text/plain"),
        },
        headers=headers,
    )


def _count(ws: Workspace) -> int:
    with workspace_context(ws.id, actor=SYSTEM):
        return Document.objects.count()


def _files() -> list[Path]:
    root = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


def _client(user: object) -> Client:
    client = Client()
    client.force_login(user)  # type: ignore[arg-type]
    return client


def test_a_member_uploads_a_pdf(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    r = _send(_client(admin), ws_a.id, make_pdf(["Een brief"]), name="C:\\fakepath\\brief.pdf")
    assert r.status_code == 201, r.content  # type: ignore[attr-defined]
    body = r.json()  # type: ignore[attr-defined]
    assert body["source"] == "upload"
    assert body["has_original"] is True
    assert body["original_name"] == "brief.pdf"
    assert body["status"] == "processing"
    assert body["page_count"] == 0
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Document.objects.get(id=body["id"]).scanned_by == admin


def test_file_uploads_have_a_lower_limit_than_scans(ws_a: Workspace, settings: Settings) -> None:
    """Security review of PR #58, point 5: a file costs the shared worker rendering and OCR
    on top of what a scan costs, so files have their own, lower limit; scans from the app
    still go through."""
    settings.BRIEVIE_FILE_UPLOADS_PER_MEMBER = "2/10m"
    client = _client(admin_of(ws_a))

    codes = [upload_file(client, ws_a.id, make_pdf([f"Brief {n}"])).status_code for n in range(3)]

    assert codes == [201, 201, 429]
    assert upload(client, ws_a.id, [jpeg("een scan")]).status_code == 201


def test_a_workspace_has_a_file_limit_of_its_own(ws_a: Workspace, settings: Settings) -> None:
    settings.BRIEVIE_FILE_UPLOADS_PER_WORKSPACE = "1/1h"
    first = _client(admin_of(ws_a))
    other = UserFactory.create()
    Membership.objects.create(user=other, workspace=ws_a, role=Membership.Role.MEMBER)

    assert upload_file(first, ws_a.id, make_pdf(["Een"])).status_code == 201
    r = upload_file(_client(other), ws_a.id, make_pdf(["Twee"]))
    assert r.status_code == 429
    assert r.json()["code"] == "too_many_uploads"


def test_the_default_file_limits_are_below_the_scan_limits() -> None:
    from apps.common.throttling import CallerThrottle

    def per_hour(rate: str) -> float:
        count, seconds = CallerThrottle("x", rate).parse_rate(rate)
        assert count is not None
        assert seconds is not None
        return count * 3600 / seconds

    assert per_hour(django_settings.BRIEVIE_FILE_UPLOADS_PER_MEMBER) < per_hour(
        django_settings.BRIEVIE_UPLOADS_PER_MEMBER
    )
    assert per_hour(django_settings.BRIEVIE_FILE_UPLOADS_PER_WORKSPACE) < per_hour(
        django_settings.BRIEVIE_UPLOADS_PER_WORKSPACE
    )


def test_a_viewer_cannot_upload(ws_a: Workspace) -> None:
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    r = _send(_client(viewer), ws_a.id, make_pdf(["Een brief"]))
    assert r.status_code == 403  # type: ignore[attr-defined]
    assert _count(ws_a) == 0


def test_a_device_token_is_refused(ws_a: Workspace) -> None:
    r = _send(Client(), ws_a.id, make_pdf(["Een brief"]), headers=bearer_for(admin_of(ws_a)))
    assert r.status_code == 401  # type: ignore[attr-defined]
    assert _count(ws_a) == 0


def test_a_session_without_csrf_is_refused(ws_a: Workspace) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_of(ws_a))
    r = _send(client, ws_a.id, make_pdf(["Een brief"]))
    assert r.status_code == 403  # type: ignore[attr-defined]
    assert _count(ws_a) == 0


def test_the_same_file_again_and_another_file(ws_a: Workspace) -> None:
    client = _client(admin_of(ws_a))
    client_id = uuid.uuid4()
    data = make_pdf(["Een brief"])
    first = _send(client, ws_a.id, data, client_id=client_id)
    again = _send(client, ws_a.id, data, client_id=client_id)
    other = _send(client, ws_a.id, make_pdf(["Iets anders"]), client_id=client_id)
    assert again.status_code == 200  # type: ignore[attr-defined]
    assert again.json()["id"] == first.json()["id"]  # type: ignore[attr-defined]
    assert other.status_code == 409  # type: ignore[attr-defined]
    assert other.json()["code"] == "document_conflict"  # type: ignore[attr-defined]


def test_a_gif_is_not_supported(ws_a: Workspace) -> None:
    r = _send(_client(admin_of(ws_a)), ws_a.id, b"GIF89a" + bytes(40), name="brief.pdf")
    assert r.status_code == 422  # type: ignore[attr-defined]
    assert r.json() == {  # type: ignore[attr-defined]
        "code": "file_type_not_supported",
        "message": "Dit bestand kan Brievie niet lezen. Gebruik een PDF, JPG of PNG.",
    }
    assert _count(ws_a) == 0
    assert _files() == []


def test_an_encrypted_pdf_is_refused(ws_a: Workspace) -> None:
    r = _send(_client(admin_of(ws_a)), ws_a.id, make_encrypted_pdf())
    assert r.status_code == 422  # type: ignore[attr-defined]
    assert r.json()["code"] == "file_encrypted"  # type: ignore[attr-defined]
    assert _count(ws_a) == 0
    assert _files() == []


def test_a_letter_with_archive_fields_is_refused(ws_a: Workspace) -> None:
    r = _send(_client(admin_of(ws_a)), ws_a.id, make_pdf(["Een brief"]), title="Polis")
    assert r.status_code == 422  # type: ignore[attr-defined]
    assert _count(ws_a) == 0


def test_an_own_document_by_upload(ws_a: Workspace) -> None:
    r = _send(
        _client(admin_of(ws_a)),
        ws_a.id,
        make_pdf(["Polisblad"]),
        kind="document",
        title="Polis inboedel",
        document_type="policy",
    )
    assert r.status_code == 201, r.content  # type: ignore[attr-defined]
    with workspace_context(ws_a.id, actor=SYSTEM):
        document = Document.objects.get(id=r.json()["id"])  # type: ignore[attr-defined]
    assert document.kind == Document.Kind.DOCUMENT
    assert document.title == "Polis inboedel"


def test_download_returns_the_exact_file(ws_a: Workspace) -> None:
    client = _client(admin_of(ws_a))
    data = make_pdf(["Een brief"])
    created = _send(client, ws_a.id, data, name="aanslag 2026.pdf").json()  # type: ignore[attr-defined]
    r = client.get(f"/api/v1/workspaces/{ws_a.id}/documents/{created['id']}/original")
    assert r.status_code == 200
    assert r.content == data
    assert r["Content-Type"] == "application/pdf"
    assert r["Cache-Control"] == "private, no-store"
    assert r["Content-Disposition"].startswith("attachment;")
    assert "aanslag" in r["Content-Disposition"]


def test_download_is_for_who_sees_the_letter(ws_a: Workspace, ws_b: Workspace) -> None:
    created = _send(_client(admin_of(ws_a)), ws_a.id, make_pdf(["Brief"])).json()  # type: ignore[attr-defined]
    stranger = _client(admin_of(ws_b))
    r = stranger.get(f"/api/v1/workspaces/{ws_a.id}/documents/{created['id']}/original")
    assert r.status_code == 404


def test_download_of_an_app_scan_is_404(ws_a: Workspace) -> None:
    client = _client(admin_of(ws_a))
    scanned = upload(client, ws_a.id, [jpeg()]).json()
    r = client.get(f"/api/v1/workspaces/{ws_a.id}/documents/{scanned['id']}/original")
    assert r.status_code == 404
    assert r.json()["code"] == "original_not_found"
    assert scanned["has_original"] is False
    assert scanned["source"] == "scan"


def test_json_body_is_refused_like_create_document(ws_a: Workspace) -> None:
    r = _client(admin_of(ws_a)).post(_url(ws_a.id), {"a": 1}, content_type="application/json")
    assert r.status_code == 415
    assert _count(ws_a) == 0

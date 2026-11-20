import json
import uuid
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.test import Client
from procrastinate.contrib.django.models import ProcrastinateJob
from pytest_django import Settings

from apps.documents import intake
from apps.documents.models import Document, DocumentPage
from apps.documents.storage import default_storage
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import (
    admin_of,
    bearer_for,
    documents_url,
    jpeg,
    metadata_for,
    upload,
)
from tests.factories import UserFactory


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def stored_files() -> list[Path]:
    root = Path(django_settings.BRIEVIE_STORAGE_ROOT)
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def counts(workspace: Workspace) -> tuple[int, int]:
    with workspace_context(workspace.id):
        return Document.objects.count(), DocumentPage.objects.count()


def test_upload_stores_the_document_encrypted_and_queues_processing(
    client_a: Client, ws_a: Workspace
) -> None:
    pages = [jpeg("een"), jpeg("twee")]

    r = upload(client_a, ws_a.id, pages, texts=["Belastingdienst", "Pagina twee"])

    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "processing"
    assert body["page_count"] == 2
    assert [p["ocr_text"] for p in body["pages"]] == ["Belastingdienst", "Pagina twee"]
    assert body["proposals"] == []
    assert body["tasks"] == []
    assert body["letter_date"] is None

    files = stored_files()
    assert len(files) == 2
    for file, page in zip(files, pages, strict=True):
        content = file.read_bytes()
        assert not content.startswith(b"\xff\xd8\xff")
        assert page[4:40] not in content

    with workspace_context(ws_a.id):
        document = Document.objects.get()
        assert document.scanned_by == admin_of(ws_a)
        assert document.ocr_text == "Belastingdienst\n\f\nPagina twee"
    job = ProcrastinateJob.objects.get()
    assert job.task_name == "process_document"
    assert job.args == {"workspace_id": str(ws_a.id), "document_id": body["id"]}


def test_uploaded_page_can_be_read_back(client_a: Client, ws_a: Workspace) -> None:
    pages = [jpeg("een"), jpeg("twee")]
    document_id = upload(client_a, ws_a.id, pages).json()["id"]

    r = client_a.get(f"{documents_url(ws_a.id)}/{document_id}/pages/2")

    assert r.status_code == 200
    assert r["Content-Type"] == "image/jpeg"
    assert r["Cache-Control"] == "private, no-store"
    assert r.content == pages[1]


def test_unknown_page_is_404(client_a: Client, ws_a: Workspace) -> None:
    document_id = upload(client_a, ws_a.id, [jpeg()]).json()["id"]

    r = client_a.get(f"{documents_url(ws_a.id)}/{document_id}/pages/2")

    assert r.status_code == 404
    assert r.json()["code"] == "page_not_found"


def test_bsn_in_the_page_text_is_masked_before_storage(client_a: Client, ws_a: Workspace) -> None:
    r = upload(client_a, ws_a.id, [jpeg()], texts=["Uw BSN 111222333 staat hier"])

    assert r.json()["pages"][0]["ocr_text"] == "Uw BSN [BSN] staat hier"
    with workspace_context(ws_a.id):
        assert "111222333" not in Document.objects.get().ocr_text
        assert "111222333" not in DocumentPage.objects.get().ocr_text


def test_same_upload_twice_gives_one_document(client_a: Client, ws_a: Workspace) -> None:
    # Review Focus 1: the app retries after a lost response.
    pages = [jpeg()]
    client_id = uuid.uuid4()

    first = upload(client_a, ws_a.id, pages, client_id=client_id)
    second = upload(client_a, ws_a.id, pages, client_id=client_id)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    assert counts(ws_a) == (1, 1)
    assert ProcrastinateJob.objects.count() == 1
    assert len(stored_files()) == 1


def test_other_upload_under_the_same_client_id_is_a_conflict(
    client_a: Client, ws_a: Workspace
) -> None:
    client_id = uuid.uuid4()
    upload(client_a, ws_a.id, [jpeg("een")], client_id=client_id)
    before = [p.read_bytes() for p in stored_files()]

    r = upload(client_a, ws_a.id, [jpeg("anders")], client_id=client_id)

    assert r.status_code == 409
    assert r.json()["code"] == "document_conflict"
    assert counts(ws_a) == (1, 1)
    assert [p.read_bytes() for p in stored_files()] == before


def test_upload_that_loses_the_race_for_a_client_id_is_not_a_500(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [jpeg()]
    client_id = uuid.uuid4()
    upload(client_a, ws_a.id, pages, client_id=client_id)
    # The first lookup misses, as when another request inserts between lookup and insert.
    monkeypatch.setattr(intake, "_find_existing", lambda workspace, client_id: None)

    r = upload(client_a, ws_a.id, pages, client_id=client_id)

    assert r.status_code == 200
    assert counts(ws_a) == (1, 1)
    assert len(stored_files()) == 1


def test_session_upload_of_a_large_page_stays_in_memory(
    ws_a: Workspace, settings: Settings, tmp_path: Path
) -> None:
    # The CSRF check reads the form before the view; nothing may reach a temp file.
    temp = tmp_path / "upload-temp"
    temp.mkdir()
    settings.FILE_UPLOAD_TEMP_DIR = str(temp)
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_of(ws_a))
    client.get("/")  # sets the CSRF cookie
    token = client.cookies["csrftoken"].value
    page = jpeg(size=3 * 1024 * 1024)

    r = upload(client, ws_a.id, [page], headers={"X-CSRFToken": token})

    assert r.status_code == 201
    assert list(temp.iterdir()) == []
    document_id = r.json()["id"]
    assert client.get(f"{documents_url(ws_a.id)}/{document_id}/pages/1").content == page


def test_session_upload_without_csrf_token_is_403(ws_a: Workspace) -> None:
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin_of(ws_a))

    r = upload(client, ws_a.id, [jpeg()])

    assert r.status_code == 403
    assert counts(ws_a) == (0, 0)


def test_device_token_uploads_without_csrf(ws_a: Workspace) -> None:
    client = Client(enforce_csrf_checks=True)

    r = upload(client, ws_a.id, [jpeg()], headers=bearer_for(admin_of(ws_a)))

    assert r.status_code == 201


def test_viewer_cannot_upload(client: Client, ws_a: Workspace) -> None:
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)

    r = upload(client, ws_a.id, [jpeg()])

    assert r.status_code == 403
    assert counts(ws_a) == (0, 0)


def test_unauthenticated_upload_is_401(client: Client, ws_a: Workspace) -> None:
    r = upload(client, ws_a.id, [jpeg()])

    assert r.status_code == 401


def rejected(client: Client, ws: Workspace, pages: list[bytes], **kwargs: object) -> str:
    r = upload(client, ws.id, pages, **kwargs)  # type: ignore[arg-type]
    assert r.status_code == 422, r.content
    assert set(r.json()) == {"code", "message"}
    assert counts(ws) == (0, 0)
    assert stored_files() == []
    assert ProcrastinateJob.objects.count() == 0
    return str(r.json()["code"])


def test_wrong_checksum_is_refused(client_a: Client, ws_a: Workspace) -> None:
    metadata = metadata_for([jpeg("anders")])

    assert rejected(client_a, ws_a, [jpeg()], metadata=metadata) == "page_checksum_mismatch"


def test_non_jpeg_is_refused(client_a: Client, ws_a: Workspace) -> None:
    png = b"\x89PNG\r\n\x1a\n" + bytes(100)

    assert rejected(client_a, ws_a, [png]) == "validation_error"


@pytest.mark.parametrize(
    "page",
    [b"\xff\xd8\xff", b"\xff\xd8\xff\xe0" + bytes(2000)],
    ids=["three bytes", "only a start"],
)
def test_a_page_that_is_no_usable_jpeg_is_refused_before_anything_is_queued(
    client_a: Client, ws_a: Workspace, page: bytes
) -> None:
    # The finding: three magic bytes were stored and sent to the paid AI service.
    # `rejected` also checks that nothing was stored or queued.
    assert rejected(client_a, ws_a, [page]) == "validation_error"


def test_page_count_must_match_the_metadata(client_a: Client, ws_a: Workspace) -> None:
    metadata = metadata_for([jpeg("een"), jpeg("twee")])

    assert rejected(client_a, ws_a, [jpeg("een")], metadata=metadata) == "validation_error"


def test_too_many_pages_are_refused(client_a: Client, ws_a: Workspace, settings: Settings) -> None:
    settings.BRIEVIE_MAX_PAGES = 2
    pages = [jpeg(str(n)) for n in range(3)]

    assert rejected(client_a, ws_a, pages) == "validation_error"


def test_too_long_page_text_is_refused(
    client_a: Client, ws_a: Workspace, settings: Settings
) -> None:
    limit = settings.BRIEVIE_MAX_PAGE_TEXT_CHARS
    assert limit == 20000

    assert rejected(client_a, ws_a, [jpeg()], texts=["x" * (limit + 1)]) == "validation_error"


@pytest.mark.parametrize(
    "metadata",
    ["geen json", "{}", json.dumps({"client_id": "x", "pages": []}), json.dumps({"pages": []})],
)
def test_bad_metadata_is_refused(client_a: Client, ws_a: Workspace, metadata: str) -> None:
    assert rejected(client_a, ws_a, [jpeg()], metadata=metadata) == "validation_error"


def test_upload_without_pages_is_refused(client_a: Client, ws_a: Workspace) -> None:
    r = client_a.post(documents_url(ws_a.id), {"metadata": json.dumps(metadata_for([]))})

    assert r.status_code == 422


def test_body_above_the_upload_limit_is_413_before_it_is_read(
    client_a: Client, ws_a: Workspace, settings: Settings
) -> None:
    # Review Focus 5
    settings.BRIEVIE_MAX_UPLOAD_BYTES = 1000

    r = upload(client_a, ws_a.id, [jpeg(size=5000)])

    assert r.status_code == 413
    assert r.json()["code"] == "payload_too_large"
    assert counts(ws_a) == (0, 0)


def test_json_body_above_the_general_limit_is_413(client: Client, settings: Settings) -> None:
    settings.BRIEVIE_MAX_BODY_BYTES = 100

    r = client.post(
        "/api/v1/auth/login",
        json.dumps({"email": "jan@example.nl", "password": "x" * 200}),
        content_type="application/json",
    )

    assert r.status_code == 413


def test_multipart_is_still_refused_elsewhere(client_a: Client, ws_a: Workspace) -> None:
    r = client_a.post(f"/api/v1/workspaces/{ws_a.id}/folders", {"name": "Auto"})

    assert r.status_code == 415


def test_storage_failure_leaves_nothing_behind(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FullDisk:
        def __init__(self) -> None:
            self.real = default_storage()
            self.writes = 0

        def write(self, path: str, data: bytes) -> None:
            self.writes += 1
            if self.writes == 2:
                raise OSError(28, "No space left on device")
            self.real.write(path, data)

        def read(self, path: str) -> bytes:
            return self.real.read(path)

        def delete(self, path: str) -> None:
            self.real.delete(path)

    disk = FullDisk()
    monkeypatch.setattr(intake, "default_storage", lambda: disk)
    client_a.raise_request_exception = False

    r = upload(client_a, ws_a.id, [jpeg("een"), jpeg("twee")])

    assert r.status_code == 500
    assert stored_files() == []
    assert counts(ws_a) == (0, 0)
    assert ProcrastinateJob.objects.count() == 0


def test_page_file_cannot_be_opened_as_another_page(client_a: Client, ws_a: Workspace) -> None:
    upload(client_a, ws_a.id, [jpeg("een"), jpeg("twee")])
    first, second = stored_files()
    second.write_bytes(first.read_bytes())
    with workspace_context(ws_a.id):
        document = Document.objects.get()
    client_a.raise_request_exception = False

    r = client_a.get(f"{documents_url(ws_a.id)}/{document.id}/pages/2")

    assert r.status_code == 500


def test_nul_character_in_page_text_is_refused(client_a: Client, ws_a: Workspace) -> None:
    r = upload(client_a, ws_a.id, [jpeg()], texts=["Belasting\x00dienst"])

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert stored_files() == []
    assert counts(ws_a) == (0, 0)


def test_failure_after_the_pages_are_written_leaves_no_files(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[Path] = []

    def queue_down(**_: object) -> None:
        written.extend(stored_files())
        raise ConnectionError("queue is down")

    monkeypatch.setattr(intake, "defer_processing", queue_down)
    client_a.raise_request_exception = False

    r = upload(client_a, ws_a.id, [jpeg("een"), jpeg("twee")])

    assert r.status_code == 500
    assert len(written) == 2
    assert stored_files() == []
    assert counts(ws_a) == (0, 0)


# --- Security review 2026-11-20: limits per member and per workspace ---


def upload_one(client: Client, workspace: Workspace) -> int:
    return upload(client, workspace.id, [jpeg(str(uuid.uuid4()))]).status_code


def test_a_member_uploading_too_much_is_asked_to_wait(
    client_a: Client, ws_a: Workspace, settings: Settings
) -> None:
    settings.BRIEVIE_UPLOADS_PER_MEMBER = "3/10m"

    assert [upload_one(client_a, ws_a) for _ in range(3)] == [201, 201, 201]
    r = upload(client_a, ws_a.id, [jpeg("vier")])

    assert r.status_code == 429
    assert r.json() == {
        "code": "too_many_uploads",
        "message": "Er zijn net heel veel brieven verstuurd. Wacht even, dan gaat het vanzelf "
        "verder.",
    }
    assert int(r["Retry-After"]) >= 1
    assert counts(ws_a) == (3, 3)
    # Another member of the workspace still uploads.
    other = UserFactory.create()
    Membership.objects.create(user=other, workspace=ws_a, role=Membership.Role.MEMBER)
    assert upload_one(Client(headers=bearer_for(other)), ws_a) == 201


def test_a_workspace_has_a_limit_of_its_own(
    client_a: Client, ws_a: Workspace, ws_b: Workspace, settings: Settings
) -> None:
    settings.BRIEVIE_UPLOADS_PER_WORKSPACE = "2/1h"
    other = UserFactory.create()
    Membership.objects.create(user=other, workspace=ws_a, role=Membership.Role.MEMBER)
    phone = Client(headers=bearer_for(other))

    assert [upload_one(client_a, ws_a), upload_one(phone, ws_a)] == [201, 201]

    assert upload_one(phone, ws_a) == 429
    assert upload_one(client_a, ws_a) == 429
    # Another workspace is not affected.
    client_b = Client()
    client_b.force_login(admin_of(ws_b))
    assert upload_one(client_b, ws_b) == 201


def test_somebody_outside_the_workspace_does_not_use_up_its_limit(
    client_a: Client, ws_a: Workspace, settings: Settings
) -> None:
    settings.BRIEVIE_UPLOADS_PER_WORKSPACE = "1/1h"
    stranger = Client(headers=bearer_for(UserFactory.create()))

    for _ in range(3):
        assert upload_one(stranger, ws_a) == 404

    assert upload_one(client_a, ws_a) == 201

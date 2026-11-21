"""Page images of a colleague's letter stay closed (S 4.10, plan Task 14).

A page is looked up through the letter the member may see before any file is touched,
no answer names where a page is stored, and a page image is never kept by a cache.
"""

import uuid
from dataclasses import dataclass, field

import pytest
from django.test import Client

from apps.common import crypto
from apps.documents import services
from apps.documents.models import DocumentPage
from apps.workspaces.context import SYSTEM
from tests.documents_support import documents_url
from tests.visibility_support import Office, as_actor, office


@dataclass
class RecordingStorage:
    """Stands in for the page store: records every path asked for."""

    read_paths: list[str] = field(default_factory=list)

    def read(self, path: str) -> bytes:
        self.read_paths.append(path)
        return b"sealed"


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> RecordingStorage:
    recorder = RecordingStorage()
    monkeypatch.setattr(services, "default_storage", lambda: recorder)
    monkeypatch.setattr(crypto, "open_sealed", lambda *_args, **_kwargs: b"\xff\xd8")
    return recorder


def _page_url(o: Office, name: str) -> str:
    return f"{documents_url(o.workspace.id)}/{o.letters[name].id}/pages/1"


def test_page_of_a_hidden_letter_is_404_before_the_file_is_opened(
    client: Client, storage: RecordingStorage
) -> None:
    o = office()
    for user in (o.hr, o.plain, o.admin, o.sorter, o.viewer):
        client.force_login(user)
        hidden = client.get(_page_url(o, "finance_letter"))
        unknown = client.get(f"{documents_url(o.workspace.id)}/{uuid.uuid4()}/pages/1")
        assert hidden.status_code == 404, user.name
        assert (
            hidden.json()
            == unknown.json()
            == {
                "code": "document_not_found",
                "message": "We kunnen dit document niet vinden.",
            }
        )
    assert storage.read_paths == []

    client.force_login(o.finance)
    assert client.get(_page_url(o, "finance_letter")).status_code == 200
    assert storage.read_paths == [f"test/{o.letters['finance_letter'].id}/1"]


def test_no_answer_holds_a_storage_path(client: Client) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        paths = list(DocumentPage.objects.values_list("storage_path", flat=True))
    assert paths
    client.force_login(o.finance)
    base = f"/api/v1/workspaces/{o.workspace.id}"
    answers = [
        client.get(documents_url(o.workspace.id)),
        client.get(f"{base}/inbox/counts"),
        client.get(f"{base}/persons"),
        client.get(f"{base}/labels"),
        client.get(f"{base}/tasks?status=all"),
        client.get(f"{base}/trash"),
        client.post(
            f"{documents_url(o.workspace.id)}/search",
            {"q": "afzender"},
            content_type="application/json",
        ),
    ]
    for name in sorted(o.visible(o.finance.id)):
        letter = o.letters[name].id
        answers.append(client.get(f"{documents_url(o.workspace.id)}/{letter}"))
        answers.append(client.get(f"{documents_url(o.workspace.id)}/{letter}/events"))
    for response in answers:
        assert response.status_code == 200, response.content
        body = response.content.decode()
        assert "storage_path" not in body
        for path in paths:
            assert path not in body


def test_page_answer_is_not_cacheable(client: Client, storage: RecordingStorage) -> None:
    o = office()
    client.force_login(o.finance)
    response = client.get(_page_url(o, "finance_letter"))
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert storage.read_paths

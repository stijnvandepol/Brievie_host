"""A private letter reaches nobody but its readers through the paths that landed after the
plan was written: the trash, duplicate hints, search, and task lists and counts. At home
everyone sees all other post, so anything private that shows up for Anne or Carla is a
leak."""

from django.test import Client

from apps.accounts.models import User
from apps.documents.models import Document
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.documents_support import pair
from tests.visibility_support import Home, as_actor, home, letter, private_letter

JSON = "application/json"


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _bobs_secret(h: Home, text: str = "") -> Document:
    doc = private_letter(h.workspace, addressees=[h.persons["Bob"]])
    if text:
        with as_actor(h.workspace, SYSTEM):
            Document.objects.filter(id=doc.id).update(ocr_text=text, sender="Huisarts Zeldzaam")
    return doc


def test_a_private_letter_in_the_trash_is_in_nobody_elses_trash() -> None:
    h = home()
    doc = _bobs_secret(h)
    base = f"/api/v1/workspaces/{h.workspace.id}"
    r = _client(h.bob).post(f"{base}/documents/{doc.id}/trash", content_type=JSON)
    assert r.status_code in (200, 201), r.json()
    for other in (h.anne, h.carla):
        body = _client(other).get(f"{base}/trash")
        assert body.status_code == 200
        assert str(doc.id) not in body.content.decode()
        assert body.json()["total"] == 0
    mine = _client(h.bob).get(f"{base}/trash").json()
    assert [item["id"] for item in mine["items"]] == [str(doc.id)]


def test_a_private_letter_gives_no_duplicate_hint_to_others() -> None:
    h = home()
    secret = _bobs_secret(h)
    visible = letter(h.workspace, scanned_by=h.carla)
    pair(secret, visible)
    base = f"/api/v1/workspaces/{h.workspace.id}"
    for other in (h.anne, h.carla):
        client = _client(other)
        duplicates = client.get(f"{base}/duplicates")
        assert duplicates.status_code == 200
        assert str(secret.id) not in duplicates.content.decode()
        assert client.get(f"{base}/inbox/counts").json()["duplicate"] == 0
        rows = {row["id"]: row for row in client.get(f"{base}/documents").json()}
        assert rows[str(visible.id)]["open_duplicate_count"] == 0
        assert str(secret.id) not in rows
    assert _client(h.bob).get(f"{base}/inbox/counts").json()["duplicate"] == 1


def test_search_never_finds_someone_elses_private_letter() -> None:
    h = home()
    doc = _bobs_secret(h, text="Uw zeldzaamheidsverklaring van de huisarts")
    url = f"/api/v1/workspaces/{h.workspace.id}/documents/search"
    for other in (h.anne, h.carla):
        r = _client(other).post(url, {"q": "zeldzaamheidsverklaring"}, content_type=JSON)
        assert r.status_code == 200, r.json()
        assert r.json()["items"] == []
        assert str(doc.id) not in r.content.decode()
    r = _client(h.bob).post(url, {"q": "zeldzaamheidsverklaring"}, content_type=JSON)
    assert [item["id"] for item in r.json()["items"]] == [str(doc.id)]


def test_task_lists_and_counts_leave_out_someone_elses_private_letter() -> None:
    h = home()
    doc = _bobs_secret(h)
    with as_actor(h.workspace, SYSTEM):
        task = Task.objects.get(document=doc)
        Task.objects.filter(id=task.id).update(description="Geheime betaling")
    base = f"/api/v1/workspaces/{h.workspace.id}"
    for other in (h.anne, h.carla):
        client = _client(other)
        for path in (
            "/tasks",
            "/my-tasks?part=all",
            "/my-tasks?part=mine",
            "/my-tasks?part=to_pick_up",
        ):
            r = client.get(f"{base}{path}")
            assert r.status_code == 200, (path, r.json())
            text = r.content.decode()
            assert str(task.id) not in text, path
            assert "Geheime betaling" not in text, path
        counts = client.get(f"{base}/my-tasks/counts").json()
        assert counts["mine"] == 0
    bob = _client(h.bob)
    assert str(task.id) in bob.get(f"{base}/tasks").content.decode()
    assert bob.get(f"{base}/my-tasks/counts").json()["mine"] == 1

"""Confirm-with-Zorg and removing a member at the same moment (plan Task 30, PB T18,
review focus 4): the one lock order (own membership `FOR SHARE` first) makes one wait for
the other, never a deadlock, and no share is left for somebody who is no member."""

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from django.db import DatabaseError, connections
from django.test import Client

from apps.documents import visibility
from apps.documents.models import Document, DocumentReader, DocumentShare
from apps.labels.models import Label
from apps.tasks.models import Task
from apps.workspaces import access
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.visibility_support import Home, as_actor, home, letter, readers

if TYPE_CHECKING:
    from tests.helpers import Response

pytestmark = pytest.mark.django_db(transaction=True)


def _review_letter(h: Home) -> Document:
    doc = letter(h.workspace, scanned_by=h.carla, status="needs_review")
    with as_actor(h.workspace, SYSTEM):
        Task.objects.filter(document=doc).delete()
    return doc


def _confirm_with_zorg(h: Home, doc: Document) -> Callable[[], "Response"]:
    with as_actor(h.workspace, SYSTEM):
        zorg = Label.objects.get(workspace=h.workspace, system_key="zorg")
    client = Client()
    client.force_login(h.bob)
    body = {
        "sender": "Huisarts",
        "document_type": "",
        "summary": "",
        "summary_by_ai": False,
        "tasks": [],
        "sets_organisation": True,
        "label_ids": [str(zorg.id)],
        "addressee_ids": [],
    }
    url = f"/api/v1/workspaces/{h.workspace.id}/documents/{doc.id}/confirm"
    return lambda: client.post(url, body, content_type="application/json")


def _remove_carla(h: Home) -> Callable[[], "Response"]:
    client = Client()
    client.force_login(h.anne)
    url = f"/api/v1/workspaces/{h.workspace.id}/members/{h.carla.id}"
    return lambda: client.delete(url)


def _in_thread(call: Callable[[], "Response"], results: list[object]) -> threading.Thread:
    def run() -> None:
        try:
            results.append(call().status_code)
        except DatabaseError as exc:  # recorded for the assertion: a deadlock must show
            results.append(exc)
        finally:
            connections.close_all()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def _no_carla(h: Home, doc: Document) -> None:
    assert not Membership.objects.filter(workspace=h.workspace, user=h.carla).exists()
    with as_actor(h.workspace, SYSTEM):
        assert not DocumentShare.objects.filter(user=h.carla).exists()
        assert not DocumentReader.objects.filter(user=h.carla).exists()
        assert Document.objects.get(id=doc.id).visibility == "restricted"
    assert ("Bob", "share") in readers(doc)


def test_confirm_with_zorg_and_removing_a_member_at_once_never_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = home()
    doc = _review_letter(h)
    confirm, remove = _confirm_with_zorg(h, doc), _remove_carla(h)
    inside, go = threading.Event(), threading.Event()
    original = visibility.make_private

    def paused(document: Document, **kwargs: object) -> str:
        inside.set()
        go.wait(10)
        return original(document, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(visibility, "make_private", paused)
    confirmed: list[object] = []
    removed: list[object] = []
    first = _in_thread(confirm, confirmed)
    assert inside.wait(10)
    second = _in_thread(remove, removed)
    time.sleep(0.5)
    assert second.is_alive()  # waiting on the memberships, which the confirm holds
    go.set()
    first.join(20)
    second.join(20)
    assert confirmed == [200]
    assert removed in ([200], [204])
    _no_carla(h, doc)


def test_removal_first_then_confirm_writes_no_share_for_the_removed_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = home()
    doc = _review_letter(h)
    confirm, remove = _confirm_with_zorg(h, doc), _remove_carla(h)
    inside, go = threading.Event(), threading.Event()
    original = access.on_member_removed

    def paused(workspace_id: UUID, user_id: UUID) -> None:
        inside.set()
        go.wait(10)
        original(workspace_id, user_id)

    monkeypatch.setattr("apps.workspaces.members.on_member_removed", paused)
    removed: list[object] = []
    confirmed: list[object] = []
    first = _in_thread(remove, removed)
    assert inside.wait(10)
    second = _in_thread(confirm, confirmed)
    time.sleep(0.5)
    assert second.is_alive()  # waiting at its own membership
    go.set()
    first.join(20)
    second.join(20)
    assert removed in ([200], [204])
    assert confirmed == [200]
    _no_carla(h, doc)

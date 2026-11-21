"""One member of two workspaces sees each on its own (navigation plan Task 11, spec S 8):
every answer of the menu, the lists, Mijn post, Blijft liggen, search, Mijn taken and the
notices of one workspace holds nothing of the other, and an id of the other workspace is
not found."""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.notices import notify
from apps.documents.models import Document, DocumentAddressee
from apps.people.services import ensure_person_for_member
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.services import create_workspace
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.visibility_support import as_actor

WORD = "isolatiewoordqq"


@dataclass(frozen=True)
class Side:
    workspace: Workspace
    letter: Document
    task: Task


@dataclass(frozen=True)
class World:
    anne: User
    home: Side
    office: Side


def _side(workspace: Workspace, anne: User, *, old: bool) -> Side:
    with as_actor(workspace, SYSTEM):
        person = ensure_person_for_member(workspace=workspace, user=anne)
        then = timezone.now() - timedelta(days=10 if old else 0)
        letter = Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            status="done",
            sender=f"Afzender {WORD}",
            addressee_ids=[person.id],
        )
        DocumentAddressee.objects.create(workspace=workspace, document=letter, person=person)
        Document.objects.filter(id=letter.id).update(created_at=then, confirmed_at=then)
        task = Task.objects.create(
            workspace=workspace, document=letter, type="pay", description="Betalen"
        )
    return Side(workspace=workspace, letter=letter, task=task)


def world() -> World:
    anne = UserFactory.create(name="Anne")
    home = create_workspace(name="Thuis", kind="home", admin=anne)
    office = create_workspace(name="Bedrijf", kind="business", admin=UserFactory.create())
    # Anne's "Ongelezen" in Bedrijf starts before its letter of ten days ago.
    Membership.objects.create(
        user=anne,
        workspace=office,
        role="member",
        sorts_post=True,
        unread_from=timezone.now() - timedelta(days=30),
    )
    result = World(
        anne=anne, home=_side(home, anne, old=False), office=_side(office, anne, old=True)
    )
    # A letter nobody opened in Bedrijf: Anne's own, not opened, 10 days old.
    with as_actor(office, SYSTEM):
        Document.objects.filter(id=result.office.letter.id).update(addressee_opened_at=None)
    notify(
        anne,
        "emergency_access_switched_on",
        actor_name="Bob",
        values={"workspace": "Bedrijf"},
        workspace_id=office.id,
    )
    notify(
        anne,
        "self_grant",
        actor_name="Bob",
        values={"category": "unassigned"},
        workspace_id=home.id,
    )
    return result


def _client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


def _get(client: Client, path: str) -> object:
    r = client.get(f"/api/v1{path}")
    assert r.status_code == 200, (path, r.content)
    return r.json()


def _ids(rows: object) -> set[str]:
    assert isinstance(rows, list)
    return {row["id"] for row in rows}


def test_one_member_of_two_workspaces_sees_each_on_its_own() -> None:
    w = world()
    client = _client(w.anne)
    for side, other in ((w.home, w.office), (w.office, w.home)):
        base = f"/workspaces/{side.workspace.id}"
        own, foreign = {str(side.letter.id)}, str(other.letter.id)

        counts = _get(client, f"{base}/menu/counts")
        assert isinstance(counts, dict)
        assert counts["my_post_unread"] == 1
        assert counts["my_tasks"] == 1
        for query in ("", "?addressed_to_me=true", "?unread_only=true"):
            assert _ids(_get(client, f"{base}/documents{query}")) == own, query

        lingering = _get(client, f"{base}/inbox/lingering")
        assert isinstance(lingering, dict)
        lingering_ids = _ids(lingering["too_long_in_inbox"] + lingering["not_opened"])
        assert foreign not in lingering_ids

        r = post_json(client, f"{base}/documents/search", {"q": WORD})
        assert r.status_code == 200
        assert {item["id"] for item in r.json()["items"]} == own

        my_tasks = _get(client, f"{base}/my-tasks?part=all")
        assert isinstance(my_tasks, dict)
        assert {item["id"] for item in my_tasks["items"]} == {str(side.task.id)}
        count = _get(client, f"{base}/my-tasks/counts")
        assert isinstance(count, dict)
        assert count["mine"] == 1

        # An id of the other workspace is not found here.
        r = post_json(client, f"{base}/documents/{foreign}/read")
        assert r.status_code == 404
        r = post_json(client, f"{base}/tasks/{other.task.id}/assign", {"user_id": None})
        assert r.status_code == 404
        r = post_json(client, f"{base}/documents/read", {"document_ids": [foreign]})
        assert r.json() == {"marked": 0}

    # "Blijft liggen" is Bedrijf's: Anne sorts there; at home it does not exist.
    office = _get(client, f"/workspaces/{w.office.workspace.id}/inbox/lingering")
    assert isinstance(office, dict)
    assert _ids(office["not_opened"]) == {str(w.office.letter.id)}
    home = _get(client, f"/workspaces/{w.home.workspace.id}/menu/counts")
    assert isinstance(home, dict)
    assert (home["shows_lingering"], home["lingering"]) == (False, 0)

    notices = _get(client, "/me/notices")
    assert isinstance(notices, list)
    assert {n["code"]: n["workspace_id"] for n in notices} == {
        "emergency_access_switched_on": str(w.office.workspace.id),
        "self_grant": str(w.home.workspace.id),
    }


def test_marking_read_in_one_workspace_leaves_the_other_unread() -> None:
    w = world()
    client = _client(w.anne)
    r = post_json(client, f"/workspaces/{w.home.workspace.id}/documents/{w.home.letter.id}/read")
    assert r.status_code == 204
    home = _get(client, f"/workspaces/{w.home.workspace.id}/menu/counts")
    office = _get(client, f"/workspaces/{w.office.workspace.id}/menu/counts")
    assert isinstance(home, dict)
    assert isinstance(office, dict)
    assert (home["my_post_unread"], office["my_post_unread"]) == (0, 1)

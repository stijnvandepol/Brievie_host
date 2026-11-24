"""Letter status follows the rule of sight (plan Task 11, Review Focus 1).

In `office()`, in every state of "Iedereen ziet alle post" and departments: no member gets
a status, a filter result, a count or a history line of a letter they may not read; a
member's snooze is theirs alone; waiting, snoozing and handling a hidden letter answer like
an unknown id.
"""

import itertools
import uuid
from datetime import date, timedelta

import pytest
from django.test import Client

from apps.documents import services
from apps.documents.models import Document, DocumentEvent
from apps.tasks import letter_sync
from apps.tasks.models import Task, TaskSnooze
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.helpers import post_json
from tests.my_tasks_support import membership
from tests.visibility_support import Office, as_actor, office

STATES = list(itertools.product((False, True), (False, True)))
STATUSES = ("new", "open", "waiting", "handled")


def _varied(o: Office) -> None:
    """Every kind of status among the letters: late, waiting, handled, and the rest open."""
    names = list(o.letters)
    with as_actor(o.workspace, SYSTEM):
        for index, name in enumerate(names):
            tasks = Task.objects.filter(document=o.letters[name])
            if index % 4 == 0:
                tasks.update(deadline=date(2000, 1, 1))
            elif index % 4 == 1:
                tasks.update(waiting_until=date.today() + timedelta(days=30))
            elif index % 4 == 2:
                tasks.update(status="done")
        letter_sync.verify_workspace(o.workspace.id)


def _live_visible(o: Office, user_id: uuid.UUID) -> set[uuid.UUID]:
    with as_actor(o.workspace, SYSTEM):
        live = set(Document.objects.filter(trashed_at__isnull=True).values_list("id", flat=True))
    names = o.visible(user_id)
    return {o.letters[n].id for n in names} & live


@pytest.mark.parametrize(("everyone", "departments"), STATES)
def test_status_counts_hide_unreadable_letters(everyone: bool, departments: bool) -> None:
    o = office()
    _varied(o)
    o.state(e=everyone, d=departments, a=False)
    for user in o.users.values():
        seen = _live_visible(o, user.id)
        with as_actor(o.workspace, user.id):
            counts = services.status_counts(membership(o.workspace, user))
        total = counts.new + counts.open + counts.waiting + counts.handled
        assert total == len(seen), (user.name, counts)
        assert counts.overdue <= counts.open + counts.waiting


@pytest.mark.parametrize(("everyone", "departments"), STATES)
def test_status_filter_never_widens(everyone: bool, departments: bool) -> None:
    o = office()
    _varied(o)
    o.state(e=everyone, d=departments, a=False)
    for user in o.users.values():
        seen = _live_visible(o, user.id)
        member = membership(o.workspace, user)
        with as_actor(o.workspace, user.id):
            unfiltered = {d.id for d in services.list_documents(member)}
            assert unfiltered == seen, user.name
            union: set[uuid.UUID] = set()
            for status in STATUSES:
                found = {d.id for d in services.list_documents(member, letter_status=status)}
                assert found <= unfiltered, (user.name, status)
                union |= found
            assert union == unfiltered, user.name
            late = {d.id for d in services.list_documents(member, overdue=True)}
            assert late <= unfiltered, user.name


def test_person_counts_hide_unreadable_letters() -> None:
    o = office()
    _varied(o)
    femke = o.persons["Femke"]
    private = o.letters["private_femke"]
    with as_actor(o.workspace, o.plain.id):
        counts = services.status_counts(membership(o.workspace, o.plain), person_id=femke.id)
        listed = services.list_documents(membership(o.workspace, o.plain), person_id=femke.id)
    assert private.id not in {d.id for d in listed}
    assert counts.new + counts.open + counts.waiting + counts.handled == len(listed)


def test_snooze_rows_are_own_only() -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        task = Task.objects.filter(document=o.letters["own_piet"]).get()
        TaskSnooze.objects.create(
            workspace=o.workspace, task=task, user=o.plain, until=date.today() + timedelta(days=3)
        )
        TaskSnooze.objects.create(
            workspace=o.workspace, task=task, user=o.admin, until=date.today() + timedelta(days=4)
        )
    with as_actor(o.workspace, o.plain.id):
        assert list(TaskSnooze.objects.values_list("user_id", flat=True)) == [o.plain.id]
        assert TaskSnooze.objects.filter(user_id=o.admin.id).delete()[0] == 0
    with as_actor(o.workspace, o.hr.id):
        # Hans reads neither Piet's letter nor has a row of his own.
        assert TaskSnooze.objects.count() == 0


def test_a_snooze_of_a_letter_no_longer_seen_is_hidden() -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    with as_actor(o.workspace, SYSTEM):
        task = Task.objects.filter(document=o.letters["finance_letter"]).get()
        TaskSnooze.objects.create(
            workspace=o.workspace, task=task, user=o.hr, until=date.today() + timedelta(days=3)
        )
    o.state(e=False, d=True, a=False)
    with as_actor(o.workspace, o.hr.id):
        assert TaskSnooze.objects.count() == 0


def test_handle_wait_and_snooze_on_hidden_letter_are_404(client: Client) -> None:
    o = office()
    hidden = o.letters["private_femke"]
    with as_actor(o.workspace, SYSTEM):
        task = Task.objects.filter(document=hidden).get()
    client.force_login(o.plain)
    base = f"/workspaces/{o.workspace.id}"
    until = (date.today() + timedelta(days=3)).isoformat()
    r = post_json(client, f"{base}/documents/{hidden.id}/handle", {"task_ids": [str(task.id)]})
    assert (r.status_code, r.json()["code"]) == (404, "document_not_found")
    r = post_json(client, f"{base}/tasks/{task.id}/waiting", {"until": until})
    assert (r.status_code, r.json()["code"]) == (404, "task_not_found")
    r = post_json(client, f"{base}/tasks/{task.id}/snooze", {"until": until})
    assert (r.status_code, r.json()["code"]) == (404, "task_not_found")
    with as_actor(o.workspace, SYSTEM):
        assert Task.objects.get(id=task.id).waiting_until is None
        assert TaskSnooze.objects.count() == 0


def test_the_message_of_a_refusal_names_no_letter(client: Client) -> None:
    o = office()
    hidden = o.letters["private_femke"]
    client.force_login(o.plain)
    r = post_json(
        client, f"/workspaces/{o.workspace.id}/documents/{hidden.id}/handle", {"task_ids": []}
    )
    assert r.status_code == 404
    assert "Femke" not in r.content.decode()


def test_letter_lines_follow_letter_sight(client: Client) -> None:
    o = office()
    private = o.letters["private_femke"]
    with as_actor(o.workspace, SYSTEM):
        for kind in ("letter_handled", "letter_reopened", "task_waiting"):
            DocumentEvent.objects.create(
                workspace=o.workspace, document=private, kind=kind, origin="system"
            )
    url = f"/api/v1/workspaces/{o.workspace.id}/documents/{private.id}/events"
    client.force_login(o.plain)
    assert client.get(url).status_code == 404
    client.force_login(o.finance)
    kinds = {row["kind"] for row in client.get(url).json()}
    assert {"letter_handled", "letter_reopened", "task_waiting"} <= kinds


def test_the_summary_of_a_letter_lists_only_for_readers(client: Client) -> None:
    o = office()
    _varied(o)
    private = o.letters["private_femke"]
    client.force_login(o.plain)
    rows = client.get(f"/api/v1/workspaces/{o.workspace.id}/documents").json()
    assert str(private.id) not in {row["id"] for row in rows}
    client.force_login(o.finance)
    rows = client.get(f"/api/v1/workspaces/{o.workspace.id}/documents").json()
    assert str(private.id) in {row["id"] for row in rows}


def test_a_viewer_gets_status_but_cannot_change_it(client: Client) -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    client.force_login(o.viewer)
    base = f"/api/v1/workspaces/{o.workspace.id}/documents"
    assert client.get(f"{base}/status-counts").status_code == 200
    assert Membership.objects.get(workspace=o.workspace, user=o.viewer).role == "viewer"
    letter = o.letters["own_piet"]
    r = post_json(client, f"/workspaces/{o.workspace.id}/documents/{letter.id}/handle", {})
    assert r.status_code == 403

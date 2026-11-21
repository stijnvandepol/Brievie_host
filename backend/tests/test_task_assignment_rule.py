"""Who may change who does a task (navigation plan Task 7, spec S 7): a task nobody has is
picked up by any member who sees it; someone else's task is handed on only by them or an
admin (409 `task_taken`); the clients get the same rule as three flags."""

import uuid
from typing import TYPE_CHECKING

import pytest
from django.test import Client

from apps.accounts.models import User
from apps.documents.models import DocumentEvent
from apps.tasks.assignment import allowed, flags_for
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.helpers import post_json
from tests.my_tasks_support import Household, household, letter

if TYPE_CHECKING:
    from tests.helpers import Response

ME = uuid.uuid4()
OTHER = uuid.uuid4()
THIRD = uuid.uuid4()


@pytest.mark.parametrize(
    ("caller_is_admin", "current", "wanted", "expected"),
    [
        # Nobody has it: take it, give it, (release: nothing changes).
        (False, None, ME, True),
        (False, None, OTHER, True),
        (False, None, None, True),
        # Mine: give it on, release it, keep it.
        (False, ME, OTHER, True),
        (False, ME, None, True),
        (False, ME, ME, True),
        # Someone else's: not for a member, in no direction; the same again is no change.
        (False, OTHER, ME, False),
        (False, OTHER, None, False),
        (False, OTHER, THIRD, False),
        (False, OTHER, OTHER, True),
        # An admin may do anything.
        (True, OTHER, ME, True),
        (True, OTHER, None, True),
        (True, OTHER, THIRD, True),
        (True, None, OTHER, True),
    ],
)
def test_allowed_table(
    caller_is_admin: bool, current: uuid.UUID | None, wanted: uuid.UUID | None, expected: bool
) -> None:
    result = allowed(caller_id=ME, caller_is_admin=caller_is_admin, current=current, wanted=wanted)
    assert result is expected


def _assign(client: Client, h: Household, task: Task, user: User | None) -> "Response":
    return post_json(
        client,
        f"/workspaces/{h.workspace.id}/tasks/{task.id}/assign",
        {"user_id": str(user.id) if user is not None else None},
    )


def _given_to(h: Household, task: Task, user: User | None) -> None:
    from tests.visibility_support import as_actor

    with as_actor(h.workspace, SYSTEM):
        Task.objects.filter(id=task.id).update(assigned_to=user)


def _assigned(h: Household, task: Task) -> uuid.UUID | None:
    from tests.visibility_support import as_actor

    with as_actor(h.workspace, SYSTEM):
        return Task.objects.get(id=task.id).assigned_to_id


def _events(h: Household) -> int:
    from tests.visibility_support import as_actor

    with as_actor(h.workspace, SYSTEM):
        return DocumentEvent.objects.filter(kind__in=["task_assigned", "task_unassigned"]).count()


def _demote(h: Household, user: User) -> None:
    Membership.objects.filter(workspace=h.workspace, user=user).update(role="member")


@pytest.mark.parametrize("wanted", ["self", "release", "third"])
def test_member_cannot_change_colleagues_task(client: Client, wanted: str) -> None:
    h = household()
    task = letter(h.workspace)
    _given_to(h, task, h.bas)
    before = _events(h)
    client.force_login(h.piet)
    target = {"self": h.piet, "release": None, "third": h.anne}[wanted]
    r = _assign(client, h, task, target)
    assert r.status_code == 409
    assert r.json() == {
        "code": "task_taken",
        "message": "Iemand anders doet deze taak al. Vraag die persoon of een beheerder om "
        "hem door te geven.",
    }
    assert _assigned(h, task) == h.bas.id
    assert _events(h) == before


def test_admin_reassigns_colleagues_task(client: Client) -> None:
    h = household()
    task = letter(h.workspace)
    _given_to(h, task, h.piet)
    client.force_login(h.anne)
    r = _assign(client, h, task, h.bas)
    assert r.status_code == 200, r.json()
    assert _assigned(h, task) == h.bas.id
    r = _assign(client, h, task, None)
    assert r.status_code == 200
    assert _assigned(h, task) is None


def test_a_demoted_admin_is_refused_under_the_lock(client: Client) -> None:
    h = household()
    task = letter(h.workspace)
    _given_to(h, task, h.piet)
    _demote(h, h.anne)
    client.force_login(h.anne)
    assert _assign(client, h, task, h.anne).status_code == 409


def test_member_takes_unassigned_task_on_readable_letter(client: Client) -> None:
    h = household()
    task = letter(h.workspace)
    client.force_login(h.piet)
    r = _assign(client, h, task, h.piet)
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["assigned_to_id"] == str(h.piet.id)
    # Now Piet's: he may give it on or release it, not take it again.
    assert (body["can_take"], body["can_hand_over"], body["can_release"]) == (False, True, True)
    # And his own he may release.
    assert _assign(client, h, task, None).status_code == 200


def test_flags_match_allowed_for_every_case() -> None:
    for role in ("member", "admin"):
        admin = role == "admin"
        for current in (None, ME, OTHER):
            flags = flags_for(caller_id=ME, role=role, assigned_to_id=current, has_letter=True)
            take, hand_over, release = (
                allowed(caller_id=ME, caller_is_admin=admin, current=current, wanted=wanted)
                for wanted in (ME, THIRD, None)
            )
            # "Ik doe dit" only on a task nobody has (and then the rule allows it).
            assert flags.can_take == (current is None)
            assert not flags.can_take or take
            # Every hand-over offered is allowed; one refused is never offered.
            assert flags.can_hand_over == hand_over
            # Releasing is offered when someone has it and the rule allows taking it back.
            assert flags.can_release == (current is not None and release)


def test_viewer_and_letterless_flags_all_false() -> None:
    for role, has_letter in (("viewer", True), ("member", False), ("admin", False)):
        flags = flags_for(caller_id=ME, role=role, assigned_to_id=None, has_letter=has_letter)
        assert (flags.can_take, flags.can_hand_over, flags.can_release) == (False, False, False)


def test_lists_and_letter_carry_the_flags(client: Client) -> None:
    h = household()
    free = letter(h.workspace, on=[h.persons["Piet"]])
    theirs = letter(h.workspace, on=[h.persons["Piet"]])
    _given_to(h, theirs, h.bas)
    client.force_login(h.piet)
    base = f"/api/v1/workspaces/{h.workspace.id}"
    rows = {row["id"]: row for row in client.get(f"{base}/tasks").json()}
    assert rows[str(free.id)]["can_take"] is True
    assert (
        rows[str(theirs.id)]["can_take"],
        rows[str(theirs.id)]["can_hand_over"],
        rows[str(theirs.id)]["can_release"],
    ) == (False, False, False)
    items = {i["id"]: i for i in client.get(f"{base}/my-tasks", {"part": "all"}).json()["items"]}
    assert items[str(free.id)]["can_take"] is True
    detail = client.get(f"{base}/documents/{theirs.document_id}").json()
    (task,) = detail["tasks"]
    assert (task["can_take"], task["can_hand_over"], task["can_release"]) == (False, False, False)
    client.force_login(h.anne)
    detail = client.get(f"{base}/documents/{theirs.document_id}").json()
    (task,) = detail["tasks"]
    assert (task["can_take"], task["can_hand_over"], task["can_release"]) == (False, True, True)

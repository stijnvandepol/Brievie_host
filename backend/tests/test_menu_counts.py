"""One call for the menu's numbers (navigation plan Task 6, spec S 9): each number is the
length of the list it stands for, and nothing hidden is counted."""

from datetime import timedelta

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.documents.models import Document
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.my_tasks_support import household
from tests.visibility_support import Office, as_actor, letter, office


def _get(client: Client, url: str) -> object:
    r = client.get(url)
    assert r.status_code == 200, r.content
    return r.json()


def _numbers(workspace: Workspace, user: User) -> dict[str, object]:
    client = Client()
    client.force_login(user)
    base = f"/api/v1/workspaces/{workspace.id}"
    counts = _get(client, f"{base}/menu/counts")
    assert isinstance(counts, dict)
    review = _get(client, f"{base}/documents?inbox_state=review")
    unassigned = _get(client, f"{base}/documents?inbox_state=unassigned")
    my_post = _get(client, f"{base}/documents?addressed_to_me=true&unread_only=true")
    my_tasks = _get(client, f"{base}/my-tasks/counts")
    lingering = _get(client, f"{base}/inbox/lingering")
    assert isinstance(review, list) and isinstance(unassigned, list)  # noqa: PT018
    assert isinstance(my_post, list) and isinstance(my_tasks, dict)  # noqa: PT018
    assert isinstance(lingering, dict)
    return {
        "counts": counts,
        "inbox": len(review) + len(unassigned),
        "my_post_unread": len(my_post),
        "my_tasks": my_tasks["mine"],
        "my_tasks_overdue": my_tasks["overdue"],
        "lingering": len(lingering["too_long_in_inbox"]) + len(lingering["not_opened"]),
    }


def _old(o: Office) -> None:
    """Every letter arrived and was confirmed 10 days ago; "Ongelezen" starts before."""
    then = timezone.now() - timedelta(days=10)
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(workspace=o.workspace).update(created_at=then, confirmed_at=then)
    Membership.objects.filter(workspace=o.workspace).update(unread_from=then - timedelta(days=1))


def test_menu_counts_equal_the_lists() -> None:
    o = office()
    _old(o)
    for user in o.users.values():
        numbers = _numbers(o.workspace, user)
        counts = numbers.pop("counts")
        assert isinstance(counts, dict)
        for name, value in numbers.items():
            assert counts[name] == value, (user.name, name)


def test_menu_counts_hide_nothing_and_count_nothing_hidden() -> None:
    o = office()
    _old(o)
    # Femke's private letter: on her own person, unread for her, hidden from the admin.
    femke = _numbers(o.workspace, o.finance)["counts"]
    assert isinstance(femke, dict)
    assert femke["my_post_unread"] >= 1
    admin = _numbers(o.workspace, o.admin)
    counts = admin["counts"]
    assert isinstance(counts, dict)
    assert counts["shows_lingering"] is True
    assert counts["lingering"] == admin["lingering"]


def test_unread_counts_follow_marks_and_person() -> None:
    h = household()
    document = letter(h.workspace, on=[h.persons["Piet"]])
    client = Client()
    client.force_login(h.piet)
    url = f"/api/v1/workspaces/{h.workspace.id}/menu/counts"
    counts = _get(client, url)
    assert isinstance(counts, dict)
    assert (counts["my_post_unread"], counts["has_person"], counts["shows_lingering"]) == (
        1,
        True,
        False,
    )
    r = client.post(
        f"/api/v1/workspaces/{h.workspace.id}/documents/{document.id}/read",
        content_type="application/json",
    )
    assert r.status_code == 204
    counts = _get(client, url)
    assert isinstance(counts, dict)
    assert counts["my_post_unread"] == 0
    # Anne's own post is not Piet's letter.
    anne = Client()
    anne.force_login(h.anne)
    counts = _get(anne, url)
    assert isinstance(counts, dict)
    assert counts["my_post_unread"] == 0

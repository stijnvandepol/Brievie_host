""" "Blijft liggen" through the seam (navigation plan Task 5, spec S 6): only in an
organisation, only for its sorters and admins, only letters they may see."""

from datetime import timedelta

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.documents import lingering
from apps.documents.models import Document
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.my_tasks_support import household
from tests.visibility_support import Office, as_actor, office


def _age(document: Document, days: int, *, opened: bool = False) -> None:
    """Arrived and confirmed `days` days ago; opened by an addressee or not."""
    then = timezone.now() - timedelta(days=days)
    with as_actor(document.workspace, SYSTEM):
        Document.objects.filter(id=document.id).update(
            created_at=then,
            confirmed_at=then if document.status == "done" else None,
            addressee_opened_at=then if opened else None,
        )


def _all_old(o: Office, days: int = 10) -> None:
    for document in o.letters.values():
        _age(document, days)


def _lingering(workspace: Workspace, user: User) -> lingering.Lingering:
    member = Membership.objects.select_related("workspace").get(workspace=workspace, user=user)
    with as_actor(workspace, user.id):
        return lingering.lingering_letters(member)


def _count(workspace: Workspace, user: User) -> int:
    member = Membership.objects.select_related("workspace").get(workspace=workspace, user=user)
    with as_actor(workspace, user.id):
        return lingering.count_lingering(member)


def _names(o: Office, documents: list[Document]) -> set[str]:
    return {o.name_of(d.id) for d in documents}


def test_home_workspace_has_no_lingering() -> None:
    h = household()
    from tests.visibility_support import letter

    old = letter(h.workspace, on=[])
    _age(old, 30)
    result = _lingering(h.workspace, h.anne)
    assert (result.too_long_in_inbox, result.not_opened) == ([], [])
    assert _count(h.workspace, h.anne) == 0


def test_plain_member_gets_empty_result() -> None:
    o = office()
    _all_old(o)
    for user in (o.plain, o.finance, o.viewer):
        result = _lingering(o.workspace, user)
        assert (result.too_long_in_inbox, result.not_opened) == ([], [])
        assert _count(o.workspace, user) == 0


def test_sorter_and_admin_get_both_groups() -> None:
    o = office()
    _all_old(o)
    sorter = _lingering(o.workspace, o.sorter)
    # Sandra sorts: the unassigned letter waits in the Inbox, and she sees the letter on
    # Jan (a person without an account, nobody reads through) not opened.
    assert "unassigned" in _names(o, sorter.too_long_in_inbox)
    assert "person_without_account" in _names(o, sorter.not_opened)
    assert _count(o.workspace, o.sorter) == len(sorter.too_long_in_inbox) + len(sorter.not_opened)
    admin = _lingering(o.workspace, o.admin)
    assert _count(o.workspace, o.admin) == len(admin.too_long_in_inbox) + len(admin.not_opened)
    # Each lists only what its viewer sees.
    for user, result in ((o.sorter, sorter), (o.admin, admin)):
        seen = o.visible(user.id)
        assert _names(o, result.too_long_in_inbox + result.not_opened) <= seen


def test_lingering_skips_letters_viewer_cannot_read() -> None:
    o = office()
    _all_old(o)
    private = o.letters["private_femke"]
    for user in (o.admin, o.sorter):
        assert "private_femke" not in o.visible(user.id)
        result = _lingering(o.workspace, user)
        assert private.id not in {d.id for d in result.too_long_in_inbox + result.not_opened}
    # Not even as a number: the count equals what the lists show.
    assert _count(o.workspace, o.admin) == sum(
        len(group)
        for group in (
            _lingering(o.workspace, o.admin).too_long_in_inbox,
            _lingering(o.workspace, o.admin).not_opened,
        )
    )


def test_opened_by_addressee_leaves_not_opened() -> None:
    o = office()
    _all_old(o)
    _age(o.letters["person_without_account"], 10, opened=True)
    assert "person_without_account" not in _names(o, _lingering(o.workspace, o.sorter).not_opened)


def test_young_letters_do_not_linger() -> None:
    o = office()
    _all_old(o, days=1)
    result = _lingering(o.workspace, o.sorter)
    assert (result.too_long_in_inbox, result.not_opened) == ([], [])


def test_thresholds_from_settings() -> None:
    o = office()
    _all_old(o, days=3)
    assert "unassigned" in _names(o, _lingering(o.workspace, o.sorter).too_long_in_inbox)
    assert _lingering(o.workspace, o.sorter).not_opened == []  # 3 < 5
    Workspace.objects.filter(id=o.workspace.id).update(
        lingering_inbox_days=4, lingering_unopened_days=3
    )
    result = _lingering(o.workspace, o.sorter)
    assert result.too_long_in_inbox == []
    assert "person_without_account" in _names(o, result.not_opened)
    assert (result.inbox_days, result.unopened_days) == (4, 3)
    Workspace.objects.filter(id=o.workspace.id).update(lingering_enabled=False)
    assert _count(o.workspace, o.sorter) == 0


def test_api_answers_rows_with_days() -> None:
    o = office()
    _all_old(o, days=6)
    client = Client()
    client.force_login(o.sorter)
    r = client.get(f"/api/v1/workspaces/{o.workspace.id}/inbox/lingering")
    assert r.status_code == 200, r.content
    body = r.json()
    assert (body["inbox_days"], body["unopened_days"]) == (2, 5)
    rows = body["too_long_in_inbox"] + body["not_opened"]
    assert rows
    assert {row["days"] for row in rows} == {6}
    assert "addressee_opened_at" not in rows[0]
    client.force_login(o.plain)
    body = client.get(f"/api/v1/workspaces/{o.workspace.id}/inbox/lingering").json()
    assert (body["too_long_in_inbox"], body["not_opened"]) == ([], [])


def _settings(
    client: Client, workspace: Workspace, body: dict[str, object]
) -> tuple[int, dict[str, object]]:
    r = client.put(
        f"/api/v1/workspaces/{workspace.id}/settings", body, content_type="application/json"
    )
    answer: dict[str, object] = r.json()
    return r.status_code, answer


def test_an_admin_changes_the_thresholds() -> None:
    o = office()
    client = Client()
    client.force_login(o.admin)
    status, body = _settings(
        client, o.workspace, {"lingering_inbox_days": 3, "lingering_unopened_days": 7}
    )
    assert status == 200, body
    assert (body["lingering_enabled"], body["lingering_inbox_days"]) == (True, 3)
    assert body["lingering_unopened_days"] == 7
    status, _body = _settings(client, o.workspace, {"lingering_inbox_days": 31})
    assert status == 422


def test_settings_without_lingering_fields_keep_them() -> None:
    o = office()
    Workspace.objects.filter(id=o.workspace.id).update(lingering_inbox_days=4)
    client = Client()
    client.force_login(o.admin)
    status, _body = _settings(client, o.workspace, {"allow_member_export": True})
    assert status == 200
    o.workspace.refresh_from_db()
    assert (o.workspace.lingering_enabled, o.workspace.lingering_inbox_days) == (True, 4)


def test_home_cannot_switch_lingering_on() -> None:
    h = household()
    client = Client()
    client.force_login(h.anne)
    status, body = _settings(client, h.workspace, {"lingering_enabled": True})
    assert status == 422
    assert body["message"] == "Dit kan alleen in een organisatie."
    h.workspace.refresh_from_db()
    assert h.workspace.lingering_enabled is False

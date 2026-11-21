"""`my_tasks` and `count_my_tasks`: derived from the post, as the member (spec S 5 to 8,
S 11; plan Task 5)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from django.utils import timezone

from apps.common.errors import ApiError
from apps.documents.models import Document, DocumentAddressee
from apps.tasks import services
from apps.tasks.facts import member_facts
from apps.tasks.mine import Group, Reason
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from tests.my_tasks_support import (
    counts,
    household,
    ids,
    letter,
    membership,
    mine,
    task_of,
)
from tests.visibility_support import office


def test_home_mine_has_own_assigned_and_unnamed_for_admins() -> None:
    h = household()
    own = letter(h.workspace, on=[h.persons["Anne"]])
    unnamed = letter(h.workspace)
    pieters = letter(h.workspace, on=[h.persons["Piet"]])
    given = letter(h.workspace, on=[h.persons["Piet"]])
    with workspace_context(h.workspace.id, actor=SYSTEM):
        Task.objects.filter(id=given.id).update(assigned_to=h.anne)

    result = mine(h.workspace, h.anne)

    assert ids(result) == {own.id, unnamed.id, given.id}
    reasons = {item.task.id: item.reason for item in result.items}
    assert reasons == {
        own.id: Reason.ADDRESSEE,
        unnamed.id: Reason.UNASSIGNED,
        given.id: Reason.ASSIGNED,
    }
    assert pieters.id not in ids(result)
    assert result.split is False
    assert ids(mine(h.workspace, h.anne, part="to_pick_up")) == set()
    # Bas sorts too: the unnamed letter is his as well.
    assert unnamed.id in ids(mine(h.workspace, h.bas))


def test_home_child_sees_only_own_post_tasks() -> None:
    h = household()
    own = letter(h.workspace, on=[h.persons["Piet"]])
    unnamed = letter(h.workspace)
    result = mine(h.workspace, h.piet)
    assert ids(result) == {own.id}
    assert unnamed.id in ids(mine(h.workspace, h.piet, part="all"))


def test_home_letter_for_person_without_account_goes_to_admins() -> None:
    h = household()
    jans = letter(h.workspace, on=[h.persons["Jan"]])
    for admin in (h.anne, h.bas):
        found = {i.task.id: i for i in mine(h.workspace, admin).items}
        assert found[jans.id].reason == Reason.UNASSIGNED
        assert found[jans.id].for_names == ["Jan"]
    assert jans.id not in ids(mine(h.workspace, h.piet))


def test_business_mine_and_to_pick_up_are_split() -> None:
    o = office()
    femke_own = task_of(o.workspace, o.letters["scanned_in_review"])
    femke_private = task_of(o.workspace, o.letters["private_femke"])
    finance = task_of(o.workspace, o.letters["finance_letter"])

    own = mine(o.workspace, o.finance)
    pick_up = mine(o.workspace, o.finance, part="to_pick_up")

    assert own.split is True
    assert ids(own) == {femke_own.id, femke_private.id}
    assert ids(pick_up) == {finance.id}
    assert [i.reason for i in pick_up.items] == [Reason.DEPARTMENT]
    assert counts(o.workspace, o.finance).to_pick_up == 1
    # The sorter picks up the post nobody reads through.
    sorter = mine(o.workspace, o.sorter, part="to_pick_up")
    assert {o.name_of(i.task.document_id) for i in sorter.items if i.task.document_id} == {
        "unassigned",
        "only_directie",
        "person_without_account",
    }


def test_department_tasks_leave_to_pick_up_when_departments_off() -> None:
    o = office()
    o.state(e=True, d=False, a=True)
    finance = task_of(o.workspace, o.letters["finance_letter"])
    assert finance.id not in ids(mine(o.workspace, o.finance, part="to_pick_up"))
    assert finance.id in ids(mine(o.workspace, o.finance, part="all"))


def test_letter_for_two_persons_listed_for_both_and_done_for_both() -> None:
    h = household()
    both = letter(h.workspace, on=[h.persons["Anne"], h.persons["Piet"]])
    for user, other in ((h.anne, "Piet"), (h.piet, "Anne")):
        found = {i.task.id: i for i in mine(h.workspace, user).items}
        assert found[both.id].for_names == [other]  # the reader is "jou"
    with workspace_context(h.workspace.id, actor=h.piet.id):
        services.complete_task(workspace=h.workspace, user=h.piet, task_id=both.id)
    for user in (h.anne, h.piet):
        assert both.id not in ids(mine(h.workspace, user))
        assert both.id in ids(mine(h.workspace, user, status="done"))


def test_all_part_lists_every_visible_task_with_reason_other() -> None:
    o = office()
    result = mine(o.workspace, o.plain, part="all")
    visible = {task_of(o.workspace, o.letters[n]).id for n in o.visible(o.plain.id)}
    assert ids(result) == visible
    reasons = {o.name_of(i.task.document_id): i.reason for i in result.items if i.task.document_id}
    assert reasons["own_piet"] == Reason.ADDRESSEE
    assert reasons["scanned_in_review"] == Reason.OTHER


def test_trashed_letters_tasks_absent() -> None:
    h = household()
    task = letter(h.workspace, on=[h.persons["Anne"]])
    with workspace_context(h.workspace.id, actor=SYSTEM):
        Document.objects.filter(tasks__id=task.id).update(trashed_at=timezone.now())
    assert task.id not in ids(mine(h.workspace, h.anne))
    assert counts(h.workspace, h.anne).mine == 0
    with workspace_context(h.workspace.id, actor=SYSTEM):
        Document.objects.filter(tasks__id=task.id).update(trashed_at=None)
    assert task.id in ids(mine(h.workspace, h.anne))


def test_hidden_letters_tasks_absent_from_items_and_counts() -> None:
    o = office()  # E off
    finance = task_of(o.workspace, o.letters["finance_letter"])
    with workspace_context(o.workspace.id, actor=SYSTEM):
        Task.objects.filter(id=finance.id).update(deadline=date(2000, 1, 1))
    for part in ("mine", "to_pick_up", "all"):
        for status in ("open", "done"):
            assert finance.id not in ids(mine(o.workspace, o.hr, part=part, status=status))
    hr_counts = counts(o.workspace, o.hr)
    assert hr_counts.overdue == 0
    assert hr_counts.to_pick_up == 1  # HR's own letter only


def test_list_follows_hand_over_without_writes() -> None:
    h = household()
    task = letter(h.workspace, on=[h.persons["Piet"]])
    assert task.id in ids(mine(h.workspace, h.piet))
    with workspace_context(h.workspace.id, actor=SYSTEM):
        before = Task.objects.get(id=task.id).updated_at
        DocumentAddressee.objects.filter(document__tasks__id=task.id).update(
            person=h.persons["Anne"]
        )
        Document.objects.filter(tasks__id=task.id).update(addressee_ids=[h.persons["Anne"].id])
    assert task.id not in ids(mine(h.workspace, h.piet))
    assert task.id in ids(mine(h.workspace, h.anne))
    with workspace_context(h.workspace.id, actor=SYSTEM):
        assert Task.objects.get(id=task.id).updated_at == before


def test_today_is_counted_in_amsterdam() -> None:
    late = datetime(2026, 11, 30, 23, 30, tzinfo=UTC)
    assert services.task_today(late) == date(2026, 12, 1)
    assert services.task_today(datetime(2026, 11, 30, 22, 30, tzinfo=UTC)) == date(2026, 11, 30)


def test_groups_and_days_left(monkeypatch: pytest.MonkeyPatch) -> None:
    h = household()
    today = date(2026, 12, 1)
    monkeypatch.setattr(services, "task_today", lambda now=None: today)
    me = [h.persons["Anne"]]
    late = letter(h.workspace, on=me, deadline=today - timedelta(days=3))
    soon = letter(h.workspace, on=me, deadline=today + timedelta(days=6))
    later = letter(h.workspace, on=me, deadline=today + timedelta(days=7))
    undated = letter(h.workspace, on=me)

    result = mine(h.workspace, h.anne)

    assert result.today == today
    found = {i.task.id: (i.group, i.days_left) for i in result.items}
    assert found == {
        late.id: (Group.OVERDUE, -3),
        soon.id: (Group.WITHIN_WEEK, 6),
        later.id: (Group.LATER, 7),
        undated.id: (Group.NO_DATE, None),
    }
    assert [i.task.id for i in result.items] == [late.id, soon.id, later.id, undated.id]
    c = counts(h.workspace, h.anne)
    assert (c.mine, c.overdue, c.within_week) == (4, 1, 1)


def test_needs_date_rows_first_in_no_date() -> None:
    h = household()
    me = [h.persons["Anne"]]
    plain = letter(h.workspace, on=me, description="Eerst gemaakt")
    term = letter(h.workspace, on=me, term_quote="binnen zes weken na dagtekening")
    result = mine(h.workspace, h.anne)
    assert [i.task.id for i in result.items] == [term.id, plain.id]
    assert counts(h.workspace, h.anne).needs_date == 1


def test_done_shows_last_30_days_newest_first() -> None:
    h = household()
    me = [h.persons["Anne"]]
    old, recent, newest = (letter(h.workspace, on=me) for _ in range(3))
    now = timezone.now()
    with workspace_context(h.workspace.id, actor=SYSTEM):
        for task, age in ((old, 31), (recent, 5), (newest, 1)):
            Task.objects.filter(id=task.id).update(
                status=Task.Status.DONE, completed_at=now - timedelta(days=age)
            )
    result = mine(h.workspace, h.anne, status="done")
    assert [i.task.id for i in result.items] == [newest.id, recent.id]
    assert all(i.group is None and i.days_left is None for i in result.items)
    assert result.waiting_letters == 0


def test_caps_and_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    h = household()
    monkeypatch.setattr(services, "MY_LIST_MAX", 3)
    for _ in range(4):
        letter(h.workspace, on=[h.persons["Anne"]])
    result = mine(h.workspace, h.anne)
    assert len(result.items) == 3
    assert result.truncated is True
    monkeypatch.setattr(services, "MY_LIST_MAX", 4)
    assert mine(h.workspace, h.anne).truncated is False


def test_counts_match_the_list() -> None:
    o = office()
    with workspace_context(o.workspace.id, actor=SYSTEM):
        tasks = list(Task.objects.order_by("id"))
        for index, task in enumerate(tasks):
            task.deadline = date(2000, 1, 1) if index % 3 == 0 else None
            task.term_quote = "binnen zes weken" if index % 3 == 1 else ""
            task.save(update_fields=["deadline", "term_quote"])
    for user in o.users.values():
        own = mine(o.workspace, user)
        c = counts(o.workspace, user)
        assert c.mine == len(own.items), user.name
        assert c.overdue == sum(i.group == Group.OVERDUE for i in own.items), user.name
        assert c.within_week == sum(i.group == Group.WITHIN_WEEK for i in own.items)
        assert c.needs_date == sum(
            i.group == Group.NO_DATE and bool(i.task.term_quote) for i in own.items
        )
        assert c.to_pick_up == len(mine(o.workspace, user, part="to_pick_up").items), user.name


def test_waiting_letters_counts_review_and_unnamed() -> None:
    h = household()
    letter(h.workspace, status="needs_review")
    letter(h.workspace)  # done, no addressee: waits for a name
    letter(h.workspace, on=[h.persons["Anne"]])
    assert mine(h.workspace, h.anne).waiting_letters == 2
    assert mine(h.workspace, h.anne, part="all").waiting_letters == 0


def test_member_without_person_has_person_false() -> None:
    h = household()
    with workspace_context(h.workspace.id, actor=SYSTEM):
        h.persons["Piet"].user = None
        h.persons["Piet"].save()
    assert mine(h.workspace, h.piet).has_person is False
    assert mine(h.workspace, h.anne).has_person is True


def test_member_facts_refuses_another_actor() -> None:
    h = household()
    with workspace_context(h.workspace.id, actor=h.anne.id), pytest.raises(RuntimeError):
        member_facts(membership(h.workspace, h.piet))
    with workspace_context(h.workspace.id, actor=SYSTEM), pytest.raises(RuntimeError):
        member_facts(membership(h.workspace, h.piet))


def test_bad_part_or_status_is_422() -> None:
    h = household()
    for part, status in (("someone", "open"), ("mine", "all")):
        with pytest.raises(ApiError) as raised:
            mine(h.workspace, h.anne, part=part, status=status)
        assert raised.value.status == 422

"""The member filter (plan Task 11, S 4.9.5): a superset of what the policy shows a member
who neither sees all nor sorts post, so the lists can use the GIN index. The policy
still decides: with or without the filter, every list and count is the same."""

from uuid import UUID

import pytest

from apps.documents import services, visibility
from apps.labels import services as labels
from apps.people import services as people
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership
from tests.visibility_support import ALL_16_STATES, Office, as_actor, office


def test_member_filter_is_none_when_everyone_sees_all(db: None) -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    for user in o.users.values():
        with as_actor(o.workspace, user.id):
            assert visibility.member_filter() is None


def test_member_filter_is_none_for_the_system_a_sorter_and_an_admin_who_reads_all(
    db: None,
) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        assert visibility.member_filter() is None
    with as_actor(o.workspace, o.sorter.id):
        assert visibility.member_filter() is None
    o.state(e=False, d=True, a=True)
    with as_actor(o.workspace, o.admin.id):
        assert visibility.member_filter() is None


def test_member_filter_applies_to_a_plain_member_in_own_post(db: None) -> None:
    o = office()
    with as_actor(o.workspace, o.plain.id):
        assert visibility.member_filter() is not None


def _everything(o: Office, member: Membership, person_id: UUID) -> tuple[object, ...]:
    lists = (
        services.list_documents(member),
        services.list_documents(member, inbox_state="unassigned"),
        services.list_documents(member, inbox_state="review"),
        services.list_documents(member, person_id=person_id),
        services.list_documents(member, automatic=True),
    )
    return (
        *([d.id for d in rows] for rows in lists),
        services.inbox_counts(member),
        [(p.id, p.document_count) for p in people.list_persons(o.workspace)],  # type: ignore[attr-defined]
        [(lb.id, lb.document_count) for lb in labels.list_labels(o.workspace)],  # type: ignore[attr-defined]
    )


@pytest.mark.parametrize("state", ALL_16_STATES)
def test_member_filter_never_changes_the_result(
    db: None, state: dict[str, bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    o = office()
    o.state(**state)
    for name, user in o.users.items():
        person_id = o.persons[user.name].id
        member = Membership.objects.get(workspace=o.workspace, user=user)
        with as_actor(o.workspace, user.id):
            with_filter = _everything(o, member, person_id)
            with monkeypatch.context() as m:
                m.setattr(visibility, "member_filter", lambda: None)
                without = _everything(o, member, person_id)
        assert with_filter == without, (state, name)


def test_lists_count_what_the_policy_shows(db: None) -> None:
    """Piet in own post: his letter and the one he scanned, counted once each."""
    o = office()
    member = Membership.objects.get(workspace=o.workspace, user=o.plain)
    with as_actor(o.workspace, o.plain.id):
        listed = {o.name_of(d.id) for d in services.list_documents(member)}
        counts = services.inbox_counts(member)
        persons = {p.display_name: p.document_count for p in people.list_persons(o.workspace)}  # type: ignore[attr-defined]
    assert listed == {"own_piet", "scanned_in_review"}
    assert counts["review"] == 1
    assert counts["unassigned"] == 0
    assert persons["Piet"] == 1
    assert persons["Femke"] == 1
    assert persons["Finance"] == 0

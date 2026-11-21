"""Managed post in the rule as data, the reader rows and the reasons (Mensen slice C, plan
Task 12). Piet manages Jan (a person without an account) in the office."""

import pytest

from apps.documents import visibility
from apps.documents.models import Document
from apps.people.models import Person
from apps.workspaces import visibility_settings
from apps.workspaces.context import SYSTEM
from apps.workspaces.policy_text import describe
from tests.test_visibility_transitions import check_policy
from tests.visibility_support import (
    ALL_16_STATES,
    Office,
    as_actor,
    office,
    private_letter,
    readers,
)


def _piet_manages_jan(o: Office) -> None:
    with as_actor(o.workspace, SYSTEM):
        Person.objects.filter(id=o.persons["Jan"].id).update(managed_by_id=o.plain.id)


def test_snapshot_counts_managed_as_own() -> None:
    o = office()
    _piet_manages_jan(o)
    policy = visibility_settings.snapshot(o.workspace)
    piet = policy.member(o.plain.id)
    assert piet is not None
    assert o.persons["Jan"].id in piet.person_ids
    assert piet.managed_ids == frozenset({o.persons["Jan"].id})
    assert o.persons["Jan"].id in policy.covered_ids


@pytest.mark.parametrize(
    "state", ALL_16_STATES, ids=lambda s: "".join(k for k, v in s.items() if v)
)
def test_expected_sight_matches_viewers_with_manager(state: dict[str, bool]) -> None:
    o = office()
    _piet_manages_jan(o)
    o.state(**state)
    sight = check_policy(o, ("managed", state))
    assert "person_without_account" in sight["Piet"]


def test_private_letter_gets_managed_reader() -> None:
    o = office()
    _piet_manages_jan(o)
    document = private_letter(o.workspace, addressees=[o.persons["Jan"]], scanned_by=o.sorter)
    assert ("Piet", "managed") in readers(document)
    # The manager goes: so does the reader row.
    with as_actor(o.workspace, SYSTEM):
        Person.objects.filter(id=o.persons["Jan"].id).update(managed_by=None)
        visibility.recompute_where(workspace_id=o.workspace.id, person_id=o.persons["Jan"].id)
    assert ("Piet", "managed") not in readers(document)


def test_recompute_for_the_manager_reaches_the_managed_letters() -> None:
    o = office()
    _piet_manages_jan(o)
    document = private_letter(o.workspace, addressees=[o.persons["Jan"]], scanned_by=o.sorter)
    with as_actor(o.workspace, SYSTEM):
        visibility.forget_member(o.workspace.id, o.plain.id)
    assert ("Piet", "managed") not in readers(document)
    with as_actor(o.workspace, SYSTEM):
        visibility.recompute_where(workspace_id=o.workspace.id, user_id=o.plain.id)
    assert ("Piet", "managed") in readers(document)


def test_viewer_reasons_names_management() -> None:
    o = office()
    _piet_manages_jan(o)
    with as_actor(o.workspace, SYSTEM):
        letter = Document.objects.get(id=o.letters["person_without_account"].id)
        reasons = visibility.viewer_reasons(letter)
    assert reasons[o.plain.id] == ["beheert de post van Jan"]


def test_policy_text_names_every_viewer_with_manager() -> None:
    o = office()
    _piet_manages_jan(o)
    policy = visibility_settings.snapshot(o.workspace)
    facts = visibility_settings.policy_facts(o.workspace, policy, o.admin.id)
    assert "Piet beheert de post van Jan." in describe(facts, audience="summary")
    member_lines = describe(
        visibility_settings.policy_facts(o.workspace, policy, o.finance.id), audience="member"
    )
    assert any("wiens post iemand beheert" in line for line in member_lines)

"""The rule of sight as data (plan Task 35, S 4.4): pure, no database."""

import ast
import dataclasses
import inspect
import uuid

import pytest

from apps.workspaces import visibility_rules
from apps.workspaces.visibility_rules import (
    LetterFacts,
    MemberFacts,
    PolicySnapshot,
    diff,
    expected_sight,
    needs_confirmation,
    sight_categories,
)
from tests.visibility_support import ALL_16_STATES
from tests.visibility_transitions import (
    NAMES,
    TRANSITIONS,
    Transition,
    department_id,
    edit_member,
    office_snapshot,
    person_id,
    uid,
)


def _cases() -> list[tuple[Transition, dict[str, bool]]]:
    return [(row, state) for row in TRANSITIONS for state in ALL_16_STATES if row.start(state)]


def _id(case: tuple[Transition, dict[str, bool]]) -> str:
    row, state = case
    return f"{row.name} [{''.join(k if v else '-' for k, v in state.items())}]"


CASES = _cases()


@pytest.mark.parametrize("case", CASES, ids=[_id(c) for c in CASES])
def test_gains_and_losses_match_the_table(case: tuple[Transition, dict[str, bool]]) -> None:
    row, state = case
    before = row.setup(office_snapshot(state))
    changes = diff(before, row.apply_to_snapshot(before))
    assert frozenset(c.name for c in changes if c.gains) == row.gains(state)
    assert frozenset(c.name for c in changes if c.loses) == row.loses(state)


@pytest.mark.parametrize("case", CASES, ids=[_id(c) for c in CASES])
def test_confirmation_matches_the_table(case: tuple[Transition, dict[str, bool]]) -> None:
    row, state = case
    before = row.setup(office_snapshot(state))
    changes = diff(before, row.apply_to_snapshot(before))
    assert needs_confirmation(changes, before=before, narrowing=row.narrowing) == row.confirm(state)


def test_every_row_runs_in_some_state() -> None:
    assert all(any(row.start(s) for s in ALL_16_STATES) for row in TRANSITIONS)


def test_everyone_sees_all_subsumes_every_category() -> None:
    for state in ALL_16_STATES:
        snapshot = office_snapshot({**state, "e": True})
        for member in snapshot.members:
            assert sight_categories(snapshot, member) == {"all"}
    # A new department member gains nothing while everyone sees all.
    before = office_snapshot({"e": True, "d": True, "a": False, "s": True})
    after = edit_member("Piet", department_ids=frozenset({department_id("Finance")}))(before)
    assert diff(before, after) == []


def test_admin_is_the_fallback_sorter_only_while_nobody_is_flagged() -> None:
    flagged = office_snapshot({"e": False, "d": True, "a": False, "s": True})
    assert flagged.sorter_ids == {uid("Sandra")}
    nobody = office_snapshot({"e": False, "d": True, "a": False, "s": False})
    assert nobody.sorter_ids == {uid("Anne")}
    anne = nobody.member(uid("Anne"))
    assert anne is not None
    assert "unassigned" in sight_categories(nobody, anne)
    anne = flagged.member(uid("Anne"))
    assert anne is not None
    assert "unassigned" not in sight_categories(flagged, anne)


def test_department_without_members_is_not_covered() -> None:
    snapshot = office_snapshot({"e": False, "d": True, "a": False, "s": True})
    assert department_id("Finance") in snapshot.covered_ids
    assert department_id("Directie") not in snapshot.covered_ids
    sandra = snapshot.member(uid("Sandra"))
    assert sandra is not None
    assert f"department:{department_id('Directie')}" in sight_categories(snapshot, sandra)
    assert f"department:{department_id('Finance')}" not in sight_categories(snapshot, sandra)


def test_departments_off_count_as_nobody() -> None:
    snapshot = office_snapshot({"e": False, "d": False, "a": False, "s": True})
    assert department_id("Finance") not in snapshot.covered_ids
    letter = LetterFacts(
        id=uuid.uuid4(),
        private=False,
        addressee_ids=frozenset({department_id("Finance")}),
        scanned_by=None,
        done=True,
        reader_ids=frozenset(),
    )
    assert expected_sight(snapshot, letter) == {uid("Sandra")}
    femke = snapshot.member(uid("Femke"))
    assert femke is not None
    assert sight_categories(snapshot, femke) == {f"person:{person_id('Femke')}"}


def test_expected_sight_never_includes_a_non_reader_for_a_private_letter() -> None:
    for state in ALL_16_STATES:
        snapshot = office_snapshot(state)
        letter = LetterFacts(
            id=uuid.uuid4(),
            private=True,
            addressee_ids=frozenset({person_id("Femke")}),
            scanned_by=uid("Piet"),
            done=False,
            reader_ids=frozenset({uid("Femke"), uuid.uuid4()}),
        )
        assert expected_sight(snapshot, letter) == {uid("Femke")}


def test_every_member_sees_own_post_and_the_scanner_his_open_scan() -> None:
    snapshot = office_snapshot({"e": False, "d": True, "a": False, "s": True})
    own = LetterFacts(
        id=uuid.uuid4(),
        private=False,
        addressee_ids=frozenset({person_id("Piet")}),
        scanned_by=uid("Vera"),
        done=False,
        reader_ids=frozenset({uid("Hans")}),
    )
    assert expected_sight(snapshot, own) == {uid("Piet"), uid("Vera"), uid("Hans")}
    done = dataclasses.replace(own, done=True, reader_ids=frozenset())
    assert expected_sight(snapshot, done) == {uid("Piet")}


def test_a_member_who_leaves_loses_and_one_who_joins_gains() -> None:
    before = office_snapshot({"e": True, "d": False, "a": True, "s": False})
    after = dataclasses.replace(
        before, members=tuple(m for m in before.members if m.name != "Vera")
    )
    [left] = diff(before, after)
    assert (left.name, left.gains, left.loses) == ("Vera", (), ("all",))
    [joined] = diff(after, before)
    assert (joined.name, joined.gains, joined.loses) == ("Vera", ("all",), ())


def test_changes_are_sorted_by_name() -> None:
    before = office_snapshot({"e": False, "d": True, "a": False, "s": True})
    after = dataclasses.replace(
        before, switches=dataclasses.replace(before.switches, everyone_sees_all=True)
    )
    assert [c.name for c in diff(before, after)] == sorted(NAMES)


def test_module_imports_no_django() -> None:
    tree = ast.parse(inspect.getsource(visibility_rules))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert modules
    assert not [m for m in modules if m.split(".")[0] in ("django", "apps")]


def test_a_person_made_or_deleted_by_the_change_counts_for_nobody() -> None:
    """Mensen M8: a fresh person (made by the change) or a deleted one is on no letter, so
    nobody gains or loses by it; a person in both snapshots still counts."""
    switches = visibility_rules.Switches(
        everyone_sees_all=False, departments_enabled=False, admins_read_all=False
    )
    piet, kept, fresh, gone = (uuid.uuid4() for _ in range(4))

    def state(person_ids: frozenset[uuid.UUID], names: dict[uuid.UUID, str]) -> PolicySnapshot:
        member = MemberFacts(
            user_id=piet,
            name="Piet",
            role="member",
            sorts_post=False,
            person_ids=person_ids,
            department_ids=frozenset(),
        )
        return PolicySnapshot(
            switches=switches, members=(member,), person_names=names, department_names={}
        )

    before = state(frozenset({gone}), {gone: "Piet (2)", kept: "Piet"})
    after = state(frozenset({kept}), {kept: "Piet"})
    (change,) = visibility_rules.diff(before, after)
    assert change.gains == (f"person:{kept}",)
    assert change.loses == ()

    before = state(frozenset({kept}), {kept: "Piet"})
    after = state(frozenset({fresh}), {kept: "Piet", fresh: "Piet (2)"})
    (change,) = visibility_rules.diff(before, after)
    assert change.gains == ()
    assert change.loses == (f"person:{kept}",)

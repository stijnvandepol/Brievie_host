"""The sentences that say who sees what (plan Task 36, S 4.2, 4.15): pure."""

import dataclasses

import pytest

from apps.workspaces.policy_text import PolicyFacts, category_phrase, describe, join_names
from apps.workspaces.visibility_rules import PolicySnapshot, Switches
from tests.visibility_support import ALL_16_STATES
from tests.visibility_transitions import department_id, office_snapshot, person_id, uid


def _facts(
    snapshot: PolicySnapshot,
    *,
    kind: str,
    viewer: str = "Anne",
    emergency_access: bool | None = None,
    ai_supplier_name: str = "",
    has_backup: bool = False,
    server_admin_names: tuple[str, ...] = (),
) -> PolicyFacts:
    return PolicyFacts(
        snapshot=snapshot,
        kind=kind,
        viewer_id=uid(viewer),
        care_is_private=kind == "home",
        emergency_access=kind != "home" if emergency_access is None else emergency_access,
        ai_supplier_name=ai_supplier_name,
        has_backup=has_backup,
        server_admin_names=server_admin_names,
    )


def _just_after_setup(snapshot: PolicySnapshot, switches: Switches) -> PolicySnapshot:
    """Only Anne, no departments: a fresh installation."""
    return dataclasses.replace(
        snapshot,
        switches=switches,
        members=tuple(m for m in snapshot.members if m.name == "Anne"),
        department_names={},
    )


def test_thuis_summary_after_setup() -> None:
    base = office_snapshot({"e": True, "d": False, "a": True, "s": False})
    snapshot = _just_after_setup(base, Switches(True, False, True))
    assert describe(_facts(snapshot, kind="home"), audience="summary") == [
        "Iedereen in Brievie ziet alle post, behalve post die privé is.",
        "Zorgpost is privé.",
        "Je bent beheerder.",
        "Brieven worden niet door AI gelezen.",
        "Er is nog geen back-up.",
    ]


def test_organisatie_summary_after_setup() -> None:
    base = office_snapshot({"e": False, "d": True, "a": False, "s": False})
    snapshot = _just_after_setup(base, Switches(False, True, False))
    assert describe(_facts(snapshot, kind="business"), audience="summary") == [
        "Ieder ziet zijn eigen post en die van zijn afdelingen.",
        "Er zijn nog geen afdelingen.",
        "Post zonder naam zie jij, tot je iemand aanwijst die post verdeelt.",
        "Beheerders lezen geen post die niet voor hen is.",
        "Noodtoegang staat aan.",
        "Brieven worden niet door AI gelezen.",
        "Er is nog geen back-up.",
    ]


def test_own_post_without_departments() -> None:
    snapshot = office_snapshot({"e": False, "d": False, "a": False, "s": True})
    lines = describe(_facts(snapshot, kind="business", has_backup=True), audience="summary")
    assert lines[0] == "Ieder ziet zijn eigen post."
    assert "Post zonder naam zien: Sandra (verdeelt post)." in lines
    assert "Er is nog geen back-up." not in lines


EVERYONE = "Iedereen in Brievie ziet alle post, behalve privé-post."
OWN_WITH_DEPARTMENTS = (
    "Je post zien alleen jij en, als hij op naam van een afdeling staat, de mensen van die "
    "afdeling."
)
OWN = "Je post zien alleen jij."
ADMINS_READ = "De beheerders Anne lezen alle post die niet privé is."
ADMINS_DO_NOT_READ = "De beheerders lezen je post niet."
SORTER = "Post zonder naam zien: Sandra (verdeelt post)."
FALLBACK = "Post zonder naam zien de beheerders: Anne."
PRIVATE = "Privé-post zie alleen jij, en wie je die brief geeft."
EMERGENCY_ON = (
    "Noodtoegang staat aan. De beheerders kunnen je post openen. Ze moeten een reden geven, "
    "en jij krijgt dan een melding."
)
EMERGENCY_OFF = "Noodtoegang staat uit."
SERVER = "Wie de server zelf beheert, kan technisch bij alle gegevens. Dat is Erik."
NO_AI = "Brieven worden niet door AI gelezen."


@pytest.mark.parametrize("emergency", [False, True])
@pytest.mark.parametrize("state", ALL_16_STATES)
def test_member_lines_for_each_state(state: dict[str, bool], emergency: bool) -> None:
    snapshot = office_snapshot(state)
    facts = _facts(
        snapshot,
        kind="business",
        viewer="Piet",
        emergency_access=emergency,
        server_admin_names=("Erik",),
    )
    lines = describe(facts, audience="member")
    e, d, a, s = state["e"], state["d"], state["a"], state["s"]
    expected = {
        EVERYONE: e,
        OWN_WITH_DEPARTMENTS: not e and d,
        OWN: not e and not d,
        ADMINS_READ: not e and a,
        ADMINS_DO_NOT_READ: not e and not a,
        SORTER: not e and s,
        FALLBACK: not e and not s,
        PRIVATE: True,
        EMERGENCY_ON: emergency,
        EMERGENCY_OFF: not emergency,
        SERVER: True,
        NO_AI: True,
    }
    for line, present in expected.items():
        assert (line in lines) == present, line
    assert len(lines) == sum(expected.values())


def test_member_lines_at_home_say_group() -> None:
    snapshot = office_snapshot({"e": False, "d": True, "a": True, "s": False})
    lines = describe(_facts(snapshot, kind="home", viewer="Piet"), audience="member")
    assert lines[0] == (
        "Je post zien alleen jij en, als hij op naam van een groep staat, de mensen van die groep."
    )


def test_ai_line_names_the_supplier() -> None:
    snapshot = office_snapshot({"e": True, "d": False, "a": True, "s": False})
    facts = _facts(snapshot, kind="home", ai_supplier_name="Mistral")
    assert "Tekst van brieven gaat naar Mistral." in describe(facts, audience="summary")
    assert "Tekst van brieven gaat naar Mistral." in describe(facts, audience="member")
    local = dataclasses.replace(facts, ai_supplier_name="", ai_local=True)
    assert "Brieven worden gelezen door AI op je eigen netwerk." in describe(
        local, audience="member"
    )


def test_names_are_joined_in_dutch() -> None:
    assert join_names([]) == ""
    assert join_names(["Erik"]) == "Erik"
    assert join_names(["Sanne", "Erik"]) == "Erik en Sanne"
    assert join_names(["Carla", "anne", "Bob"]) == "anne, Bob en Carla"


def test_category_phrases() -> None:
    snapshot = office_snapshot({"e": False, "d": True, "a": False, "s": True})
    assert category_phrase("all", snapshot) == "alle post die niet privé is"
    assert category_phrase("all_as_admin", snapshot) == "alle post als beheerder"
    assert category_phrase(f"department:{department_id('Finance')}", snapshot) == (
        "post van Finance"
    )
    assert category_phrase(f"person:{person_id('Jan')}", snapshot) == "post van Jan"
    assert category_phrase("unassigned", snapshot) == "post zonder naam"

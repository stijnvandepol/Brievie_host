"""The table of S 4.4 ("Transitions") as data, for the pure tests (Task 35) and the
database tests (Task 39).

Each row works on the office of `tests.visibility_support.office()`: Anne (admin), Sandra
(member, the flagged sorter while `s`), Femke (Finance), Hans (HR), Piet, Vera (viewer);
persons for each of them and Jan (no account); departments Finance, HR and Directie
(empty). A state is the dict of `ALL_16_STATES` (`e`, `d`, `a`, `s`).

A row says, per state, who gains and who loses (by name), and whether the admin must
confirm, written from the table's own words, not computed by `visibility_rules`.
"""

import dataclasses
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

from apps.workspaces.visibility_rules import MemberFacts, PolicySnapshot, Switches

type State = Mapping[str, bool]
type Names = frozenset[str]

NAMES: Final = ("Anne", "Sandra", "Femke", "Hans", "Piet", "Vera")
_NS: Final = uuid.UUID("6d1c0f3e-2c33-4d0b-9a3f-5d9e1b7c4a20")


def uid(name: str) -> uuid.UUID:
    """A fixed id per name: users, persons ("person:<name>") and departments."""
    return uuid.uuid5(_NS, name)


def person_id(name: str) -> uuid.UUID:
    return uid(f"person:{name}")


def department_id(name: str) -> uuid.UUID:
    return uid(f"department:{name}")


DEPARTMENTS: Final = ("Finance", "HR", "Directie")


def office_snapshot(state: State) -> PolicySnapshot:
    """The office's snapshot in `state`, without a database."""
    roles = {"Anne": "admin", "Vera": "viewer"}
    departments = {"Femke": "Finance", "Hans": "HR"}
    members = tuple(
        MemberFacts(
            user_id=uid(name),
            name=name,
            role=roles.get(name, "member"),
            sorts_post=name == "Sandra" and state["s"],
            person_ids=frozenset({person_id(name)}),
            department_ids=frozenset(
                {department_id(departments[name])} if name in departments else set()
            ),
        )
        for name in NAMES
    )
    return PolicySnapshot(
        switches=Switches(
            everyone_sees_all=state["e"], departments_enabled=state["d"], admins_read_all=state["a"]
        ),
        members=members,
        person_names={person_id(n): n for n in (*NAMES, "Jan")},
        department_names={department_id(n): n for n in DEPARTMENTS},
    )


# --- pure edits of a snapshot -----------------------------------------------------------


def switches(**values: bool) -> Callable[[PolicySnapshot], PolicySnapshot]:
    def apply(snapshot: PolicySnapshot) -> PolicySnapshot:
        return dataclasses.replace(
            snapshot, switches=dataclasses.replace(snapshot.switches, **values)
        )

    return apply


def edit_member(
    name: str,
    *,
    role: str | None = None,
    sorts_post: bool | None = None,
    person_ids: frozenset[uuid.UUID] | None = None,
    department_ids: frozenset[uuid.UUID] | None = None,
) -> Callable[[PolicySnapshot], PolicySnapshot]:
    def edit(m: MemberFacts) -> MemberFacts:
        return MemberFacts(
            user_id=m.user_id,
            name=m.name,
            role=m.role if role is None else role,
            sorts_post=m.sorts_post if sorts_post is None else sorts_post,
            person_ids=m.person_ids if person_ids is None else person_ids,
            department_ids=m.department_ids if department_ids is None else department_ids,
        )

    def apply(snapshot: PolicySnapshot) -> PolicySnapshot:
        members = tuple(edit(m) if m.name == name else m for m in snapshot.members)
        return dataclasses.replace(snapshot, members=members)

    return apply


def remove(name: str) -> Callable[[PolicySnapshot], PolicySnapshot]:
    def apply(snapshot: PolicySnapshot) -> PolicySnapshot:
        return dataclasses.replace(
            snapshot, members=tuple(m for m in snapshot.members if m.name != name)
        )

    return apply


def both(
    *steps: Callable[[PolicySnapshot], PolicySnapshot],
) -> Callable[[PolicySnapshot], PolicySnapshot]:
    def apply(snapshot: PolicySnapshot) -> PolicySnapshot:
        for step in steps:
            snapshot = step(snapshot)
        return snapshot

    return apply


def _same(snapshot: PolicySnapshot) -> PolicySnapshot:
    return snapshot


# --- who sees all, who sorts --------------------------------------------------------------


def sees_all(name: str, state: State) -> bool:
    return state["e"] or (state["a"] and name == "Anne")


def sorter(state: State) -> str:
    """Sandra when flagged, else the admin."""
    return "Sandra" if state["s"] else "Anne"


def sorters_not_seeing_all(state: State) -> Names:
    name = sorter(state)
    return frozenset() if sees_all(name, state) else frozenset({name})


def everybody_but_full_sight(state: State) -> Names:
    return frozenset(n for n in NAMES if not sees_all(n, state))


def _nobody(_state: State) -> Names:
    return frozenset()


def _never(_state: State) -> bool:
    return False


def _always(_state: State) -> bool:
    return True


def only(*names: str) -> Callable[[State], Names]:
    return lambda _state: frozenset(names)


@dataclass(frozen=True)
class Transition:
    name: str
    start: Callable[[State], bool]
    apply_to_snapshot: Callable[[PolicySnapshot], PolicySnapshot]
    gains: Callable[[State], Names]
    loses: Callable[[State], Names]
    confirm: Callable[[State], bool]
    narrowing: bool = False
    # Brought about before the row, in both the snapshot and the database.
    setup: Callable[[PolicySnapshot], PolicySnapshot] = _same
    # Every member hears of it (admins gain all post, R37).
    everyone_notified: bool = False
    # The row that undoes this one, for "switching back restores sight".
    reverse: str = ""
    # The audit codes the row writes (Task 39).
    audit: tuple[str, ...] = field(default_factory=tuple)


TRANSITIONS: Final[list[Transition]] = [
    Transition(
        name="E off -> on",
        start=lambda st: not st["e"],
        apply_to_snapshot=switches(everyone_sees_all=True),
        gains=everybody_but_full_sight,
        loses=_nobody,
        confirm=_always,
        reverse="E on -> off",
        audit=("workspace_setting_changed",),
    ),
    Transition(
        name="E on -> off",
        start=lambda st: st["e"],
        apply_to_snapshot=switches(everyone_sees_all=False),
        gains=_nobody,
        loses=lambda st: frozenset(n for n in NAMES if not (st["a"] and n == "Anne")),
        confirm=_never,
        reverse="E off -> on",
        audit=("workspace_setting_changed",),
    ),
    Transition(
        name="D off -> on, E off",
        start=lambda st: not st["e"] and not st["d"],
        apply_to_snapshot=switches(departments_enabled=True),
        gains=only("Femke", "Hans"),
        loses=sorters_not_seeing_all,
        confirm=_always,
        reverse="D on -> off, E off",
        audit=("workspace_setting_changed",),
    ),
    Transition(
        name="D on -> off, E off",
        start=lambda st: not st["e"] and st["d"],
        apply_to_snapshot=switches(departments_enabled=False),
        gains=sorters_not_seeing_all,
        loses=only("Femke", "Hans"),
        confirm=lambda st: bool(sorters_not_seeing_all(st)),
        reverse="D off -> on, E off",
        audit=("workspace_setting_changed",),
    ),
    Transition(
        name="D off -> on, E on",
        start=lambda st: st["e"] and not st["d"],
        apply_to_snapshot=switches(departments_enabled=True),
        gains=_nobody,
        loses=_nobody,
        confirm=_never,
        reverse="D on -> off, E on",
        audit=("workspace_setting_changed",),
    ),
    Transition(
        name="D on -> off, E on",
        start=lambda st: st["e"] and st["d"],
        apply_to_snapshot=switches(departments_enabled=False),
        gains=_nobody,
        loses=_nobody,
        confirm=_never,
        reverse="D off -> on, E on",
        audit=("workspace_setting_changed",),
    ),
    Transition(
        name="A off -> on, E off",
        start=lambda st: not st["e"] and not st["a"],
        apply_to_snapshot=switches(admins_read_all=True),
        gains=only("Anne"),
        loses=_nobody,
        confirm=_always,
        everyone_notified=True,
        reverse="A on -> off, E off",
        audit=("workspace_setting_changed",),
    ),
    Transition(
        name="A on -> off, E off",
        start=lambda st: not st["e"] and st["a"],
        apply_to_snapshot=switches(admins_read_all=False),
        gains=_nobody,
        loses=only("Anne"),
        confirm=_never,
        reverse="A off -> on, E off",
        audit=("workspace_setting_changed",),
    ),
    Transition(
        name="member made admin, A on, E off",
        start=lambda st: not st["e"] and st["a"],
        apply_to_snapshot=edit_member("Piet", role="admin"),
        gains=only("Piet"),
        loses=_nobody,
        confirm=_always,
        everyone_notified=True,
        reverse="admin made member, A on, E off",
        audit=("member_role_changed",),
    ),
    Transition(
        name="admin made member, A on, E off",
        start=lambda st: not st["e"] and st["a"],
        setup=edit_member("Piet", role="admin"),
        apply_to_snapshot=edit_member("Piet", role="member"),
        gains=_nobody,
        loses=only("Piet"),
        confirm=_never,
        narrowing=True,
        audit=("member_role_changed",),
    ),
    Transition(
        name="sorter added, E off",
        start=lambda st: not st["e"],
        apply_to_snapshot=edit_member("Piet", sorts_post=True),
        gains=only("Piet"),
        loses=lambda st: frozenset() if st["s"] else sorters_not_seeing_all(st),
        confirm=_always,
        reverse="sorter removed, E off",
        audit=("post_sorter_changed",),
    ),
    Transition(
        name="sorter removed, E off",
        start=lambda st: not st["e"],
        setup=edit_member("Piet", sorts_post=True),
        apply_to_snapshot=edit_member("Piet", sorts_post=False),
        gains=lambda st: frozenset() if st["s"] else sorters_not_seeing_all(st),
        loses=only("Piet"),
        confirm=lambda st: not st["s"] and not st["a"],
        narrowing=True,
        audit=("post_sorter_changed",),
    ),
    Transition(
        name="last sorter removed, E off",
        start=lambda st: not st["e"] and st["s"],
        apply_to_snapshot=edit_member("Sandra", sorts_post=False),
        gains=lambda st: frozenset() if st["a"] else frozenset({"Anne"}),
        loses=only("Sandra"),
        confirm=lambda st: not st["a"],
        narrowing=True,
        audit=("post_sorter_changed",),
    ),
    Transition(
        name="member added to a department, D on, E off",
        start=lambda st: not st["e"] and st["d"],
        apply_to_snapshot=edit_member("Piet", department_ids=frozenset({department_id("Finance")})),
        gains=only("Piet"),
        loses=_nobody,
        confirm=_always,
        reverse="member removed from a department, D on, E off",
        audit=("department_member_added",),
    ),
    Transition(
        name="member removed from a department, D on, E off",
        start=lambda st: not st["e"] and st["d"],
        setup=edit_member("Piet", department_ids=frozenset({department_id("Finance")})),
        apply_to_snapshot=edit_member("Piet", department_ids=frozenset()),
        gains=_nobody,
        loses=only("Piet"),
        confirm=_never,
        narrowing=True,
        audit=("department_member_removed",),
    ),
    Transition(
        name="member added to an empty department, D on, E off",
        start=lambda st: not st["e"] and st["d"],
        apply_to_snapshot=edit_member(
            "Piet", department_ids=frozenset({department_id("Directie")})
        ),
        gains=only("Piet"),
        loses=sorters_not_seeing_all,
        confirm=_always,
        audit=("department_member_added",),
    ),
    Transition(
        name="last department member removed",
        start=_always,
        apply_to_snapshot=edit_member("Femke", department_ids=frozenset()),
        gains=lambda st: frozenset() if st["e"] or not st["d"] else sorters_not_seeing_all(st),
        loses=lambda st: frozenset() if st["e"] or not st["d"] else frozenset({"Femke"}),
        confirm=_never,
        narrowing=True,
        audit=("department_member_removed",),
    ),
    Transition(
        name="person linked to a member, E off",
        start=lambda st: not st["e"],
        setup=edit_member("Piet", person_ids=frozenset()),
        apply_to_snapshot=edit_member("Piet", person_ids=frozenset({person_id("Piet")})),
        gains=only("Piet"),
        loses=sorters_not_seeing_all,
        confirm=_always,
        audit=("person_linked",),
    ),
    Transition(
        name="person unlinked, E off",
        start=lambda st: not st["e"],
        apply_to_snapshot=edit_member("Piet", person_ids=frozenset()),
        gains=sorters_not_seeing_all,
        loses=only("Piet"),
        confirm=_never,
        narrowing=True,
        reverse="person linked to a member, E off",
        audit=("person_unlinked",),
    ),
    Transition(
        name="member made admin, A off, nobody flagged",
        start=lambda st: not st["e"] and not st["a"] and not st["s"],
        apply_to_snapshot=edit_member("Piet", role="admin"),
        gains=only("Piet"),
        loses=_nobody,
        confirm=_always,
        reverse="admin made member, A off, nobody flagged",
        audit=("member_role_changed",),
    ),
    Transition(
        name="admin made member, A off, nobody flagged",
        start=lambda st: not st["e"] and not st["a"] and not st["s"],
        setup=edit_member("Piet", role="admin"),
        apply_to_snapshot=edit_member("Piet", role="member"),
        gains=_nobody,
        loses=only("Piet"),
        confirm=_never,
        narrowing=True,
        audit=("member_role_changed",),
    ),
    Transition(
        name="sorter made viewer",
        start=lambda st: not st["e"] and st["s"],
        apply_to_snapshot=edit_member("Sandra", role="viewer", sorts_post=False),
        gains=lambda st: frozenset() if st["a"] else frozenset({"Anne"}),
        loses=only("Sandra"),
        confirm=lambda st: not st["a"],
        narrowing=True,
        audit=("member_role_changed",),
    ),
    Transition(
        name="one of several sorters removed",
        start=lambda st: not st["e"] and st["s"],
        setup=edit_member("Piet", sorts_post=True),
        apply_to_snapshot=edit_member("Sandra", sorts_post=False),
        gains=_nobody,
        loses=only("Sandra"),
        confirm=_never,
        narrowing=True,
        audit=("post_sorter_changed",),
    ),
    Transition(
        name="member removed from the workspace",
        start=_always,
        apply_to_snapshot=remove("Femke"),
        gains=lambda st: frozenset() if st["e"] else sorters_not_seeing_all(st),
        loses=only("Femke"),
        confirm=_never,
        narrowing=True,
        audit=("member_removed",),
    ),
    Transition(
        name="kind switched without taking values",
        start=_always,
        apply_to_snapshot=_same,
        gains=_nobody,
        loses=_nobody,
        confirm=_never,
        audit=("workspace_setting_changed",),
    ),
    Transition(
        name="Organisatie preset applied to Thuis",
        start=lambda st: st["e"] and not st["d"] and st["a"],
        apply_to_snapshot=switches(
            everyone_sees_all=False, departments_enabled=True, admins_read_all=False
        ),
        gains=_nobody,
        loses=only(*NAMES),
        confirm=_never,
        reverse="Thuis preset applied to Organisatie",
        audit=("workspace_setting_changed", "workspace_preset_applied"),
    ),
    Transition(
        name="Thuis preset applied to Organisatie",
        start=lambda st: not st["e"] and st["d"] and not st["a"],
        apply_to_snapshot=switches(
            everyone_sees_all=True, departments_enabled=False, admins_read_all=True
        ),
        gains=only(*NAMES),
        loses=_nobody,
        confirm=_always,
        reverse="Organisatie preset applied to Thuis",
        audit=("workspace_setting_changed", "workspace_preset_applied"),
    ),
]

BY_NAME: Final = {t.name: t for t in TRANSITIONS}
REVERSIBLE_TRANSITIONS: Final = [t for t in TRANSITIONS if t.reverse]

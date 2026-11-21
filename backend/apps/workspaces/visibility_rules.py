"""The rule of sight as data (spec S 4.1, 4.4): who sees which kind of post, and what a
change does to that.

Pure: no Django, no database (like `apps.rules.engine`). `visibility_settings` builds a
`PolicySnapshot` from the tables before and after a change and asks `diff` who gains or
loses, and `needs_confirmation` whether the admin must confirm first. `expected_sight`
states S 4.1 letter by letter for the tests, beside the SQL of `visibility_sql`.

A category is a kind of post a member sees:

- `all`: every letter that is not private ("Iedereen ziet alle post");
- `all_as_admin`: the same, as an admin while "Beheerders lezen alle post" is on;
- `person:<id>`: the letters on a person's name (own post, the post of a person the
  member manages, or as sorter a person without an account nobody manages);
- `department:<id>`: the letters on a department's name (a member while departments
  are on, or as sorter a department nobody reads through);
- `unassigned`: the letters on nobody's name (sorters only).

`all` and `all_as_admin` each hold every other category: a member who sees all post
gains nothing by a new category, and loses only the whole when it goes.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

ADMIN: Final = "admin"
ALL: Final = "all"
ALL_AS_ADMIN: Final = "all_as_admin"
UNASSIGNED: Final = "unassigned"
PERSON: Final = "person"
DEPARTMENT: Final = "department"
# The categories that hold every letter that is not private.
EVERYTHING: Final = frozenset({ALL, ALL_AS_ADMIN})
# What a sorter gains when post nobody reads falls to them: never a confirmation for
# who already sorted (P6).
_FALLBACK_KINDS: Final = frozenset({UNASSIGNED, PERSON, DEPARTMENT})


@dataclass(frozen=True)
class Switches:
    everyone_sees_all: bool
    departments_enabled: bool
    admins_read_all: bool


@dataclass(frozen=True)
class MemberFacts:
    user_id: UUID
    name: str
    role: str
    sorts_post: bool
    # The persons linked to the member (kind person) and those without an account the
    # member manages ("Beheerd door"), and the departments they are in.
    person_ids: frozenset[UUID]
    department_ids: frozenset[UUID]
    # Of `person_ids`, the persons the member manages (for wording only: their post is the
    # member's like their own).
    managed_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True)
class PolicySnapshot:
    """Everything that decides sight of a letter that is not private, at one moment."""

    switches: Switches
    members: tuple[MemberFacts, ...]
    # Persons of kind person and departments, archived ones included.
    person_names: Mapping[UUID, str]
    department_names: Mapping[UUID, str]

    def member(self, user_id: UUID) -> MemberFacts | None:
        return next((m for m in self.members if m.user_id == user_id), None)

    @property
    def covered_ids(self) -> frozenset[UUID]:
        """Addressees somebody reads through: persons linked to a member, and departments
        with members while departments are on (S 4.1 rule 4)."""
        covered: set[UUID] = set()
        for member in self.members:
            covered |= member.person_ids
            if self.switches.departments_enabled:
                covered |= member.department_ids
        return frozenset(covered)

    @property
    def sorter_ids(self) -> frozenset[UUID]:
        """Who sorts post: the flagged members, else the admins."""
        flagged = frozenset(m.user_id for m in self.members if m.sorts_post)
        if flagged:
            return flagged
        return frozenset(m.user_id for m in self.members if m.role == ADMIN)


@dataclass(frozen=True)
class LetterFacts:
    id: UUID
    private: bool
    addressee_ids: frozenset[UUID]
    scanned_by: UUID | None
    done: bool
    # Unexpired reader rows: shares, emergency access, and a private letter's readers.
    reader_ids: frozenset[UUID]


def _sees_all(snapshot: PolicySnapshot, member: MemberFacts) -> bool:
    switches = snapshot.switches
    return switches.everyone_sees_all or (switches.admins_read_all and member.role == ADMIN)


def _my_post_ids(snapshot: PolicySnapshot, member: MemberFacts) -> frozenset[UUID]:
    if snapshot.switches.departments_enabled:
        return member.person_ids | member.department_ids
    return member.person_ids


def expected_sight(snapshot: PolicySnapshot, letter: LetterFacts) -> frozenset[UUID]:
    """The members who see `letter` by S 4.1 rules 1-6 (for the tests)."""
    if letter.private:
        return frozenset(m.user_id for m in snapshot.members if m.user_id in letter.reader_ids)
    covered = snapshot.covered_ids
    sorters = snapshot.sorter_ids
    unread = not (letter.addressee_ids & covered)
    seen: set[UUID] = set()
    for member in snapshot.members:
        if (
            _sees_all(snapshot, member)
            or letter.addressee_ids & _my_post_ids(snapshot, member)
            or (member.user_id in sorters and unread)
            or (letter.scanned_by == member.user_id and not letter.done)
            or member.user_id in letter.reader_ids
        ):
            seen.add(member.user_id)
    return frozenset(seen)


def person_category(person_id: UUID) -> str:
    return f"{PERSON}:{person_id}"


def department_category(department_id: UUID) -> str:
    return f"{DEPARTMENT}:{department_id}"


def split_category(category: str) -> tuple[str, UUID | None]:
    """("person", id), ("department", id), or (the category, None)."""
    kind, _sep, rest = category.partition(":")
    if kind in (PERSON, DEPARTMENT) and rest:
        return kind, UUID(rest)
    return category, None


def sight_categories(snapshot: PolicySnapshot, member: MemberFacts) -> frozenset[str]:
    """The kinds of post `member` sees (see the module's text)."""
    switches = snapshot.switches
    if switches.everyone_sees_all:
        return frozenset({ALL})
    if switches.admins_read_all and member.role == ADMIN:
        return frozenset({ALL_AS_ADMIN})
    categories = {person_category(p) for p in member.person_ids}
    if switches.departments_enabled:
        categories |= {department_category(d) for d in member.department_ids}
    if member.user_id in snapshot.sorter_ids:
        covered = snapshot.covered_ids
        categories.add(UNASSIGNED)
        categories |= {person_category(p) for p in snapshot.person_names if p not in covered}
        categories |= {
            department_category(d) for d in snapshot.department_names if d not in covered
        }
    return frozenset(categories)


_ORDER: Final = {ALL: 0, ALL_AS_ADMIN: 1, UNASSIGNED: 2, DEPARTMENT: 3, PERSON: 4}


def ordered(categories: Iterable[str]) -> tuple[str, ...]:
    """Broadest first: all, all as admin, unassigned (what makes a sorter), departments,
    persons. A notice names the first."""
    return tuple(sorted(categories, key=lambda c: (_ORDER.get(split_category(c)[0], 9), c)))


@dataclass(frozen=True)
class Change:
    user_id: UUID
    name: str
    gains: tuple[str, ...]
    loses: tuple[str, ...]


def _compare(before: frozenset[str], after: frozenset[str]) -> tuple[set[str], set[str]]:
    had_all = bool(before & EVERYTHING)
    has_all = bool(after & EVERYTHING)
    if had_all and has_all:
        return set(), set()
    if had_all:
        return set(), set(before & EVERYTHING)
    if has_all:
        return set(after & EVERYTHING), set()
    return set(after - before), set(before - after)


def _persons_in_both(category: str, before: PolicySnapshot, after: PolicySnapshot) -> bool:
    """False for the post of a person that exists in only one of the two snapshots: one
    made or deleted by the change itself. Such a person is on no letter (a person on a
    letter is never deleted, and a new one is on none yet), so nobody gains or loses a
    letter by it: Mensen gives a member a fresh person when they unlink their own, and
    linking takes that unused person away again (spec M8)."""
    kind, item = split_category(category)
    if kind != PERSON or item is None:
        return True
    return item in before.person_names and item in after.person_names


def diff(before: PolicySnapshot, after: PolicySnapshot) -> list[Change]:
    """Per member, the categories gained and lost between two snapshots, sorted by name.

    A member only in `before` loses everything, one only in `after` gains everything;
    members whose sight stays the same are left out. The post of a person made or deleted
    by the change counts for nobody (`_persons_in_both`)."""
    changes: list[Change] = []
    ids = [m.user_id for m in before.members] + [
        m.user_id for m in after.members if before.member(m.user_id) is None
    ]
    for user_id in ids:
        old = before.member(user_id)
        new = after.member(user_id)
        had = sight_categories(before, old) if old is not None else frozenset()
        has = sight_categories(after, new) if new is not None else frozenset()
        gains, loses = _compare(had, has)
        gains = {c for c in gains if _persons_in_both(c, before, after)}
        loses = {c for c in loses if _persons_in_both(c, before, after)}
        if not gains and not loses:
            continue
        name = new.name if new is not None else old.name if old is not None else ""
        changes.append(
            Change(user_id=user_id, name=name, gains=ordered(gains), loses=ordered(loses))
        )
    return sorted(changes, key=lambda c: (c.name.casefold(), str(c.user_id)))


def _fallback_gain(change: Change, before: PolicySnapshot) -> bool:
    """Every gain is post nobody reads, falling to a member who sorted post already."""
    if change.user_id not in before.sorter_ids:
        return False
    return all(split_category(c)[0] in _FALLBACK_KINDS for c in change.gains)


def needs_confirmation(
    changes: Sequence[Change], *, before: PolicySnapshot, narrowing: bool
) -> bool:
    """Whether anybody gains sight (S 4.4 point 2), except when the change narrows
    (an unlink, a department member removed, a demotion, a sorter unflagged) and every
    gain is a fallback gain of a member who already sorted post (P6)."""
    gaining = [c for c in changes if c.gains]
    if not gaining:
        return False
    if not narrowing:
        return True
    return not all(_fallback_gain(c, before) for c in gaining)

"""The measurement's world as planned: the shape of S 4.9.5, exactly. No database."""

from collections import Counter

import pytest

from tests.perf.data import ADMINS, DEFAULT, PLAIN, SORTER, Plan, Shape, make_plan


@pytest.fixture(scope="module")
def plan() -> Plan:
    return make_plan()


def test_the_plan_is_the_same_every_run(plan: Plan) -> None:
    again = make_plan()
    assert again.letters[:50] == plan.letters[:50]
    assert again.readers == plan.readers


def test_letters_and_the_private_share(plan: Plan) -> None:
    assert len(plan.letters) == 100_000
    assert sum(x.private for x in plan.letters) == 10_000


def test_addressee_patterns_are_exact(plan: Plan) -> None:
    def pattern(persons: tuple[int, ...], departments: tuple[int, ...]) -> str:
        return {(1, 0): "person", (0, 1): "department", (1, 1): "both", (0, 0): "none"}[
            (len(persons), len(departments))
        ]

    counts = Counter(pattern(x.persons, x.departments) for x in plan.letters)
    assert counts == {"person": 60_000, "department": 25_000, "both": 10_000, "none": 5_000}


def test_private_letters_are_spread_over_the_patterns(plan: Plan) -> None:
    # Not all on one kind of addressee: the private share is drawn apart from the pattern.
    private = [x for x in plan.letters if x.private]
    with_department = sum(bool(x.departments) for x in private) / len(private)
    assert 0.30 < with_department < 0.40  # 35 % of all letters are on a department


def test_half_the_person_letters_are_on_a_person_with_an_account(plan: Plan) -> None:
    on_person = [x.persons[0] for x in plan.letters if x.persons]
    linked = sum(p < DEFAULT.members for p in on_person) / len(on_person)
    assert 0.45 < linked < 0.55
    assert max(on_person) == DEFAULT.members + DEFAULT.unlinked_persons - 1


def test_members_roles_and_departments(plan: Plan) -> None:
    assert len(plan.roles) == 25
    assert [m for m, role in enumerate(plan.roles) if role == "admin"] == list(ADMINS)
    assert len(plan.departments) == 5
    assert all(len(set(d)) == 5 for d in plan.departments)
    in_departments = {m for d in plan.departments for m in d}
    for outside in (*ADMINS, SORTER, PLAIN):
        assert outside not in in_departments
    member = plan.department_member
    assert sum(member in d for d in plan.departments) == 1
    assert plan.roles[member] == "member"


def test_statuses_and_scanners(plan: Plan) -> None:
    statuses = Counter(x.status for x in plan.letters)
    assert statuses == {"done": 85_000, "needs_review": 8_000, "processing": 5_000, "failed": 2_000}
    by_sorter = sum(x.scanned_by == SORTER for x in plan.letters) / len(plan.letters)
    assert 0.68 < by_sorter < 0.75
    # The plain member scanned some letters that are not done yet (the scanner arm).
    assert any(x.scanned_by == PLAIN and x.status != "done" for x in plan.letters)


def test_label_links_are_unique_and_exact(plan: Plan) -> None:
    assert len(plan.label_links) == 100_000
    assert len(set(plan.label_links)) == 100_000
    assert {label for _, label in plan.label_links} == set(range(20))


def test_tasks(plan: Plan) -> None:
    assert len(plan.tasks) == 20_000
    letterless = [t for t in plan.tasks if t.letter is None]
    assert len(letterless) == 200
    assert all(t.created_by is not None for t in letterless)
    on_letters = [t.letter for t in plan.tasks if t.letter is not None]
    assert len(set(on_letters)) == len(on_letters)


def test_every_private_letter_has_an_addressee_and_a_share_reader(plan: Plan) -> None:
    by_letter: dict[int, list[str]] = {}
    for reader in plan.readers:
        by_letter.setdefault(reader.letter, []).append(reader.reason)
    private = {i for i, x in enumerate(plan.letters) if x.private}
    assert private <= set(by_letter)
    assert all(sorted(by_letter[i]) == ["addressee", "share"] for i in private)
    # About 1 % of the other letters is shared with one member (shares on every letter).
    public = {i: r for i, r in by_letter.items() if i not in private}
    assert all(reasons == ["share"] for reasons in public.values())
    assert 800 < len(public) < 1000
    shares = [r for r in plan.readers if r.reason == "share"]
    first = {r.letter: r.member for r in plan.readers if r.reason == "addressee"}
    assert all(r.member != first[r.letter] for r in shares if r.letter in first)
    # The plain member reads some private letters, as anyone would.
    assert any(r.member == PLAIN for r in plan.readers)


def test_a_mix_must_add_up_to_100() -> None:
    with pytest.raises(ValueError, match="100"):
        make_plan(Shape(letters=100, addressee_mix=(("person", 50), ("none", 40))))


def test_departments_must_fit_the_members() -> None:
    with pytest.raises(ValueError, match="departments"):
        make_plan(Shape(letters=100, department_size=6))


def test_the_autopilot_left_its_marks(plan: Plan) -> None:
    assert 9_000 < len(plan.automatic_addressees) < 11_000
    assert 9_000 < len(plan.automatic_labels) < 11_000
    assert max(plan.automatic_labels) < len(plan.label_links)
    assert all(plan.letters[i].status == "done" for i in plan.by_ai)
    assert 3_800 < len(plan.by_ai) < 4_700


def test_the_extras_leave_the_rest_of_the_world_as_it_was() -> None:
    # The autopilot marks come from their own generator: the letters, links and
    # private reader rows are the ones measured before they were added.
    plan = make_plan(Shape(letters=1_000, label_links=1_000, tasks=200, letterless_tasks=2))
    plain = make_plan(
        Shape(
            letters=1_000,
            label_links=1_000,
            tasks=200,
            letterless_tasks=2,
            automatic_percent=0,
            by_ai_percent=0,
            public_share_percent=0,
        )
    )
    assert plan.letters == plain.letters
    assert plan.label_links == plain.label_links
    assert plan.readers[: len(plain.readers)] == plain.readers


def test_read_marks_are_of_addressees_and_the_sorter(plan: Plan) -> None:
    assert len(set(plan.read_marks)) == len(plan.read_marks)
    for i, member in plan.read_marks[:5_000]:
        letter = plan.letters[i]
        addressed = {p for p in letter.persons if p < DEFAULT.members}
        for d in letter.departments:
            addressed |= set(plan.departments[d])
        assert member in addressed or member == SORTER
    by_sorter = sum(m == SORTER for _, m in plan.read_marks)
    assert 15_000 < by_sorter < 25_000
    # About 70 % of what members are addressed: well over a hundred thousand marks.
    assert 140_000 < len(plan.read_marks) < 190_000
    # The plain member has read part of their own post, not all of it.
    own = {i for i, x in enumerate(plan.letters) if PLAIN in x.persons}
    read = {i for i, m in plan.read_marks if m == PLAIN}
    assert read < own
    assert 0.6 < len(read) / len(own) < 0.8


def test_unopened_letters_are_confirmed_and_addressed(plan: Plan) -> None:
    assert all(
        plan.letters[i].status == "done"
        and (plan.letters[i].persons or plan.letters[i].departments)
        for i in plan.unopened
    )
    addressed_done = sum(
        x.status == "done" and bool(x.persons or x.departments) for x in plan.letters
    )
    assert 0.02 < len(plan.unopened) / addressed_done < 0.04

"""The transition test (plan Task 39, S 4.17's first, second and ninth tests).

Every row of S 4.4 (`tests/visibility_transitions.py`), in every start state where it
applies, run through the real services on the office: before and after, every member sees
through the policy exactly what `visibility_rules.expected_sight` says, and `viewers()`
says the same; the preview asks for a confirmation exactly when the table does; the
notices go to exactly who gains and who loses; the row's audit rows exist; nothing a
switch should keep is deleted; the private letter keeps its readers. Then: switching back
restores the earlier sight, no state leaves a letter without a viewer, and a member loses
sight of a letter on every path at once.
"""

import random
import uuid
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Final

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import AccountNotice
from apps.audit.models import WorkspaceAuditEvent
from apps.audit.services import AuditActor
from apps.documents import visibility
from apps.documents.models import Document, DocumentLabel, DocumentReader, MemberExport
from apps.labels.models import Label
from apps.people import services as people
from apps.people.models import DepartmentMember, Person
from apps.rules.models import Rule
from apps.tasks.models import Task
from apps.workspaces import members, visibility_settings
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from apps.workspaces.visibility_rules import LetterFacts, expected_sight
from apps.workspaces.visibility_settings import Proposal
from tests.documents_support import pair
from tests.test_leak_document_operations import (
    DOCUMENT_OPERATION_CASES,
    _answer,
    _ids,
    _snapshot,
)
from tests.visibility_support import ALL_16_STATES, Office, as_actor, office
from tests.visibility_transitions import BY_NAME, REVERSIBLE_TRANSITIONS, TRANSITIONS, Transition

if TYPE_CHECKING:
    from tests.helpers import Response

# --- the office in the database -----------------------------------------------------------


def _letter_facts(o: Office) -> list[LetterFacts]:
    now = timezone.now()
    with as_actor(o.workspace, SYSTEM):
        readers: dict[uuid.UUID, set[uuid.UUID]] = {}
        for document_id, user_id, expires_at in DocumentReader.objects.values_list(
            "document_id", "user_id", "expires_at"
        ):
            if expires_at is None or expires_at > now:
                readers.setdefault(document_id, set()).add(user_id)
        return [
            LetterFacts(
                id=d.id,
                private=d.visibility == Document.Visibility.RESTRICTED,
                addressee_ids=frozenset(d.addressee_ids),
                scanned_by=d.scanned_by_id,
                done=d.status == Document.Status.DONE,
                reader_ids=frozenset(readers.get(d.id, ())),
            )
            for d in Document.objects.filter(workspace=o.workspace)
        ]


def _sight(o: Office) -> dict[str, set[str]]:
    """Per member's name: the letters the policy shows them."""
    names = {
        m.user_id: m.user.name
        for m in Membership.objects.filter(workspace=o.workspace).select_related("user")
    }
    return {name: o.visible(user_id) for user_id, name in names.items()}


def check_policy(o: Office, why: object) -> dict[str, set[str]]:
    """The policy, `expected_sight` and `viewers()` agree for every member and letter."""
    policy = visibility_settings.snapshot(o.workspace)
    facts = _letter_facts(o)
    sight = _sight(o)
    with as_actor(o.workspace, SYSTEM):
        seen_by = visibility.viewers([f.id for f in facts])
    for member in policy.members:
        expected = {o.name_of(f.id) for f in facts if member.user_id in expected_sight(policy, f)}
        assert sight[member.name] == expected, (why, member.name)
        through_viewers = {o.name_of(f.id) for f in facts if member.user_id in seen_by[f.id]}
        assert through_viewers == expected, (why, member.name, "viewers()")
    return sight


# --- every row, through its real service --------------------------------------------------

type Step = Callable[[Office, Client, bool, str], object]


def _admin(o: Office) -> AuditActor:
    return AuditActor.for_user(o.admin)


def _switch(**values: bool | str) -> Step:
    def step(o: Office, _client: Client, confirmed: bool, token: str) -> object:
        proposal = Proposal(
            kind=str(values["kind"]) if "kind" in values else None,
            everyone_sees_all=_flag(values, "everyone_sees_all"),
            departments_enabled=_flag(values, "departments_enabled"),
            admins_read_all=_flag(values, "admins_read_all"),
            apply_preset=bool(values.get("apply_preset", False)),
        )
        return visibility_settings.change(
            workspace=o.workspace,
            proposal=proposal,
            actor=_admin(o),
            confirmed=confirmed,
            preview_token=token,
        )

    return step


def _flag(values: dict[str, bool | str], name: str) -> bool | None:
    value = values.get(name)
    return None if value is None else bool(value)


def _role(who: str, role: str) -> Step:
    def step(o: Office, _client: Client, confirmed: bool, token: str) -> object:
        return members.change_role(
            workspace=o.workspace,
            user_id=o.users[who].id,
            role=role,
            actor=_admin(o),
            confirmed=confirmed,
            preview_token=token,
        )

    return step


def _sorts(who: str, value: bool) -> Step:
    def step(o: Office, _client: Client, confirmed: bool, token: str) -> object:
        return members.set_sorts_post(
            workspace=o.workspace,
            user_id=o.users[who].id,
            sorts_post=value,
            actor=_admin(o),
            confirmed=confirmed,
            preview_token=token,
        )

    return step


def _department(who: str, department: str, *, add: bool) -> Step:
    def step(o: Office, _client: Client, confirmed: bool, token: str) -> object:
        with as_actor(o.workspace, o.admin.id):
            if add:
                return people.add_department_member(
                    workspace=o.workspace,
                    person_id=o.persons[department].id,
                    user_id=o.users[who].id,
                    actor=_admin(o),
                    confirmed=confirmed,
                    preview_token=token,
                )
            return people.remove_department_member(
                workspace=o.workspace,
                person_id=o.persons[department].id,
                user_id=o.users[who].id,
                actor=_admin(o),
            )

    return step


def _link(*, link: bool) -> Step:
    """Anne links Piet's person to Piet; Piet unlinks it himself (only he may, PB T12)."""

    def step(o: Office, _client: Client, confirmed: bool, token: str) -> object:
        actor = o.admin if link else o.plain
        with as_actor(o.workspace, actor.id):
            return people.link_member(
                workspace=o.workspace,
                person_id=o.persons["Piet"].id,
                user_id=o.plain.id if link else None,
                actor=AuditActor.for_user(actor),
                actor_role="admin" if link else "member",
                confirmed=confirmed,
                preview_token=token,
            )

    return step


def _remove_femke(o: Office, client: Client, _confirmed: bool, _token: str) -> object:
    client.force_login(o.admin)
    response: Response = client.delete(
        f"/api/v1/workspaces/{o.workspace.id}/members/{o.finance.id}"
    )
    assert response.status_code == 204, response.json()
    return response


def _preview_removal(o: Office, _client: Client, _confirmed: bool, _token: str) -> object:
    members.removal_preview_step(workspace=o.workspace, user_id=o.finance.id, actor=_admin(o))
    return None


STEPS: Final[dict[str, Step]] = {
    "E off -> on": _switch(everyone_sees_all=True),
    "E on -> off": _switch(everyone_sees_all=False),
    "D off -> on, E off": _switch(departments_enabled=True),
    "D on -> off, E off": _switch(departments_enabled=False),
    "D off -> on, E on": _switch(departments_enabled=True),
    "D on -> off, E on": _switch(departments_enabled=False),
    "A off -> on, E off": _switch(admins_read_all=True),
    "A on -> off, E off": _switch(admins_read_all=False),
    "member made admin, A on, E off": _role("plain", "admin"),
    "admin made member, A on, E off": _role("plain", "member"),
    "sorter added, E off": _sorts("plain", True),
    "sorter removed, E off": _sorts("plain", False),
    "last sorter removed, E off": _sorts("sorter", False),
    "member added to a department, D on, E off": _department("plain", "Finance", add=True),
    "member removed from a department, D on, E off": _department("plain", "Finance", add=False),
    "member added to an empty department, D on, E off": _department("plain", "Directie", add=True),
    "last department member removed": _department("finance", "Finance", add=False),
    "person linked to a member, E off": _link(link=True),
    "person unlinked, E off": _link(link=False),
    "member made admin, A off, nobody flagged": _role("plain", "admin"),
    "admin made member, A off, nobody flagged": _role("plain", "member"),
    "sorter made viewer": _role("sorter", "viewer"),
    "one of several sorters removed": _sorts("sorter", False),
    "member removed from the workspace": _remove_femke,
    "kind switched without taking values": _switch(kind="home"),
    "Organisatie preset applied to Thuis": _switch(kind="business", apply_preset=True),
    "Thuis preset applied to Organisatie": _switch(kind="home", apply_preset=True),
}
PREVIEW_STEPS: Final[dict[str, Step]] = {"member removed from the workspace": _preview_removal}


def _setup(o: Office, row: Transition) -> None:
    """The row's own start, written directly (as its pure `setup`)."""
    if row.name.startswith("admin made member"):
        Membership.objects.filter(workspace=o.workspace, user=o.plain).update(role="admin")
    if row.name in ("sorter removed, E off", "one of several sorters removed"):
        Membership.objects.filter(workspace=o.workspace, user=o.plain).update(sorts_post=True)
    if row.name == "member removed from a department, D on, E off":
        with as_actor(o.workspace, SYSTEM):
            DepartmentMember.objects.create(
                workspace=o.workspace, department=o.persons["Finance"], user=o.plain
            )
    if row.name == "person linked to a member, E off":
        with as_actor(o.workspace, SYSTEM):
            Person.objects.filter(id=o.persons["Piet"].id).update(user=None)


def _counts(o: Office) -> tuple[int, int, int]:
    with as_actor(o.workspace, SYSTEM):
        return (
            DepartmentMember.objects.count(),
            Label.objects.count(),
            Rule.objects.count(),
        )


def _sight_notices(o: Office) -> tuple[set[str], set[str], set[str]]:
    """Names with a sight_widened, a sight_narrowed and an admins_read_all_on notice."""
    rows = AccountNotice.objects.filter(user__memberships__workspace=o.workspace).values_list(
        "code", "user__name"
    )
    found: dict[str, set[str]] = {"sight_widened": set(), "sight_narrowed": set()}
    found["admins_read_all_on"] = set()
    for code, name in rows:
        if code in found:
            found[code].add(name)
    return found["sight_widened"], found["sight_narrowed"], found["admins_read_all_on"]


def _audit_counts(o: Office) -> Counter[str]:
    with as_actor(o.workspace, SYSTEM):
        return Counter(
            WorkspaceAuditEvent.objects.filter(workspace_id=o.workspace.id).values_list(
                "code", flat=True
            )
        )


# Rows that move no department membership, label or rule (S 4.4 point 5).
_KEEPS_EVERY_ROW: Final = frozenset(
    {
        "workspace_setting_changed",
        "member_role_changed",
        "post_sorter_changed",
        "person_linked",
        "person_unlinked",
    }
)


def _private_viewers(o: Office) -> frozenset[uuid.UUID]:
    letter = o.letters["private_femke"]
    with as_actor(o.workspace, SYSTEM):
        return visibility.viewers([letter.id])[letter.id]


def _cases() -> list[tuple[Transition, dict[str, bool]]]:
    return [(row, state) for row in TRANSITIONS for state in ALL_16_STATES if row.start(state)]


def _case_id(case: tuple[Transition, dict[str, bool]]) -> str:
    row, state = case
    return f"{row.name} [{''.join(k if v else '-' for k, v in state.items())}]"


CASES: Final = _cases()


def test_every_row_has_a_step() -> None:
    assert set(STEPS) == {row.name for row in TRANSITIONS}


@pytest.mark.parametrize("case", CASES, ids=[_case_id(c) for c in CASES])
def test_transition(case: tuple[Transition, dict[str, bool]], client: Client) -> None:
    row, state = case
    o = office()
    o.state(**state)
    _setup(o, row)
    before = check_policy(o, (row.name, "before"))
    counts = _counts(o)
    private = _private_viewers(o)
    audit_before = _audit_counts(o)

    step = STEPS[row.name]
    preview_step = PREVIEW_STEPS.get(row.name, step)
    result = visibility_settings.preview(
        o.workspace,
        lambda: preview_step(o, client, True, ""),
        narrowing=row.narrowing,
    )
    assert result.needs_confirmation == row.confirm(state)
    assert check_policy(o, (row.name, "after the preview")) == before
    assert _sight_notices(o) == (set(), set(), set())
    assert _audit_counts(o) == audit_before

    step(o, client, True, result.preview_token)

    after = check_policy(o, (row.name, "after"))
    widened, narrowed, everyone = _sight_notices(o)
    assert widened == row.gains(state)
    # A member who left is not told what they no longer see.
    gone = set(before) - set(after)
    assert narrowed == row.loses(state) - gone
    assert everyone == (set(after) if row.everyone_notified else set())
    audit_after = _audit_counts(o)
    for code in row.audit:
        assert audit_after[code] > audit_before[code], code
    if set(row.audit) <= _KEEPS_EVERY_ROW:
        assert _counts(o) == counts
    else:
        assert _counts(o)[1:] == counts[1:]
    if not gone:
        assert _private_viewers(o) == private


def _token(o: Office) -> str:
    return visibility_settings.fingerprint(visibility_settings.snapshot(o.workspace))


@pytest.mark.parametrize("row", REVERSIBLE_TRANSITIONS, ids=lambda r: r.name)
def test_switch_back_restores_sight(row: Transition, client: Client) -> None:
    back = BY_NAME[row.reverse]
    states = [s for s in ALL_16_STATES if row.start(s)]
    for state in states:
        o = office()
        o.state(**state)
        _setup(o, row)
        before = _sight(o)
        counts = _counts(o)
        STEPS[row.name](o, client, True, _token(o))
        STEPS[back.name](o, client, True, _token(o))
        assert _sight(o) == before, (row.name, state)
        assert _counts(o)[1:] == counts[1:], (row.name, state)


def test_no_orphan_in_any_state() -> None:
    """300 random states over the office's letters: every letter that is not private has
    a viewer, a private one only its readers."""
    rng = random.Random(20261120)  # noqa: S311 - a fixed seed for a test, no secret
    o = office()
    departments = [o.persons[n] for n in ("Finance", "HR", "Directie")]
    sorting = [o.sorter, o.finance, o.hr, o.plain]
    linked = {name: o.persons[name] for name in ("Sandra", "Femke", "Hans", "Piet", "Vera")}
    private = o.letters["private_femke"]
    for _ in range(300):
        Workspace.objects.filter(id=o.workspace.id).update(
            everyone_sees_all=rng.random() < 0.2,
            departments_enabled=rng.random() < 0.5,
            admins_read_all=rng.random() < 0.5,
        )
        Membership.objects.filter(workspace=o.workspace).update(sorts_post=False)
        flagged = [u.id for u in sorting if rng.random() < 0.3]
        Membership.objects.filter(workspace=o.workspace, user_id__in=flagged).update(
            sorts_post=True
        )
        with as_actor(o.workspace, SYSTEM):
            DepartmentMember.objects.all().delete()
            for department in departments:
                for user in o.users.values():
                    if rng.random() < 0.25:
                        DepartmentMember.objects.create(
                            workspace=o.workspace, department=department, user=user
                        )
            for name, person in linked.items():
                user = next(u for u in o.users.values() if u.name == name)
                Person.objects.filter(id=person.id).update(
                    user=user if rng.random() < 0.7 else None
                )
            seen = visibility.viewers([d.id for d in o.letters.values()])
            readers = set(
                DocumentReader.objects.filter(document=private).values_list("user_id", flat=True)
            )
        for name, letter in o.letters.items():
            if letter.id == private.id:
                assert seen[letter.id] == readers, name
            else:
                assert seen[letter.id], name


def test_viewers_equal_the_policy_in_every_transition(client: Client) -> None:
    """`check_policy` compares `viewers()` with the policy in `test_transition`; here once
    more on the states the table never reaches by itself: several sorters, an admin in a
    department, a viewer who scanned a letter."""
    o = office()
    Membership.objects.filter(workspace=o.workspace, user=o.plain).update(sorts_post=True)
    with as_actor(o.workspace, SYSTEM):
        DepartmentMember.objects.create(
            workspace=o.workspace, department=o.persons["HR"], user=o.admin
        )
        Document.objects.filter(id=o.letters["unassigned"].id).update(
            scanned_by=o.viewer, status=Document.Status.NEEDS_REVIEW
        )
    for state in ALL_16_STATES:
        o.state(**state)
        Membership.objects.filter(workspace=o.workspace, user=o.plain).update(sorts_post=True)
        check_policy(o, state)


# --- every path, at once ------------------------------------------------------------------


def _hide_hr_letter_from_piet(o: Office, client: Client) -> None:
    """From "Iedereen ziet alle post" to own post, through the real service."""
    client.force_login(o.admin)
    response = client.put(
        f"/api/v1/workspaces/{o.workspace.id}/visibility",
        {"everyone_sees_all": False},
        content_type="application/json",
    )
    assert response.status_code == 200, response.json()


@pytest.mark.parametrize("operation", sorted(DOCUMENT_OPERATION_CASES))
def test_every_document_operation_follows_the_switch(operation: str, client: Client) -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    letter = o.letters["hr_letter"]
    assert "hr_letter" in o.visible(o.plain.id)
    _hide_hr_letter_from_piet(o, client)
    assert "hr_letter" not in o.visible(o.plain.id)
    ids = _ids(o, letter)
    case = DOCUMENT_OPERATION_CASES[operation]
    piet = Client()
    piet.force_login(o.plain)
    before = _snapshot(o, letter)
    hidden = _answer(case(piet, ids, letter.id))
    unknown = _answer(case(piet, ids, uuid.uuid4()))
    assert hidden == unknown
    assert hidden[0] in (403, 404), hidden
    assert _snapshot(o, letter) == before


def _names(o: Office, items: list[dict[str, object]], key: str = "id") -> set[str]:
    known = {str(d.id): name for name, d in o.letters.items()}
    return {known[str(item[key])] for item in items if str(item[key]) in known}


def test_lists_counts_search_trash_duplicates_and_tasks_follow_the_switch(
    client: Client,
) -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    ws = f"/api/v1/workspaces/{o.workspace.id}"
    hr = o.letters["hr_letter"]
    trashed = o.letters["only_directie"]
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=hr.id).update(
            ocr_text="Brief over de vergaderzaal.", sender="Lantaarnpaal"
        )
        Document.objects.filter(id=trashed.id).update(trashed_at=timezone.now(), trashed_by=o.admin)
        label = DocumentLabel.objects.filter(document=hr).values_list("label_id", flat=True)[0]
        Task.objects.filter(document=hr).update(assigned_to=None)
    pair(hr, o.letters["own_piet"])
    piet = Client()
    piet.force_login(o.plain)

    def look() -> dict[str, object]:
        listed = piet.get(f"{ws}/documents").json()
        search = piet.post(
            f"{ws}/documents/search", {"q": "vergaderzaal"}, content_type="application/json"
        ).json()
        trash = piet.get(f"{ws}/trash").json()
        duplicates = piet.get(f"{ws}/duplicates").json()
        tasks = piet.get(f"{ws}/tasks").json()
        my_tasks = piet.get(f"{ws}/my-tasks", {"part": "all"}).json()["items"]
        labels = {item["id"]: item["document_count"] for item in piet.get(f"{ws}/labels").json()}
        persons = {item["id"]: item["document_count"] for item in piet.get(f"{ws}/persons").json()}
        return {
            "list": "hr_letter" in _names(o, listed),
            "search": "hr_letter" in _names(o, search["items"]),
            "trash": "only_directie" in _names(o, _items(trash)),
            "duplicates": bool(duplicates),
            "tasks": bool({str(t.id) for t in _hr_tasks(o)} & {str(t["id"]) for t in tasks}),
            "my_tasks": bool({str(t.id) for t in _hr_tasks(o)} & {str(t["id"]) for t in my_tasks}),
            "label_count": labels[str(label)],
            "hr_count": persons[str(o.persons["HR"].id)],
        }

    seen = look()
    assert all(seen[k] for k in ("list", "search", "trash", "duplicates", "tasks", "my_tasks"))
    _hide_hr_letter_from_piet(o, client)
    gone = look()
    for key in ("list", "search", "trash", "duplicates", "tasks", "my_tasks"):
        assert gone[key] is False, key
    assert int(str(gone["label_count"])) < int(str(seen["label_count"]))
    assert gone["hr_count"] == 0


def _items(body: object) -> list[dict[str, object]]:
    """A list answer, or the `items` of a paged one."""
    if isinstance(body, dict):
        items = body["items"]
        assert isinstance(items, list)
        return items
    assert isinstance(body, list)
    return body


def _hr_tasks(o: Office) -> list[Task]:
    with as_actor(o.workspace, SYSTEM):
        return list(Task.objects.filter(document=o.letters["hr_letter"]))


def test_unlinking_ends_the_members_own_export(client: Client) -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        MemberExport.objects.create(
            workspace=o.workspace,
            user=o.plain,
            storage_path=f"exports/{o.workspace.id}/{uuid.uuid4()}.sealed",
        )
    piet = Client()
    piet.force_login(o.plain)
    r = piet.put(
        f"/api/v1/workspaces/{o.workspace.id}/persons/{o.persons['Piet'].id}/member",
        {"user_id": None},
        content_type="application/json",
    )
    assert r.status_code == 200, r.json()
    with as_actor(o.workspace, SYSTEM):
        assert not MemberExport.objects.filter(user=o.plain).exists()


def test_a_notice_never_names_a_letter(client: Client) -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    _hide_hr_letter_from_piet(o, client)
    ids = {str(d.id) for d in o.letters.values()}
    for notice in AccountNotice.objects.filter(user__in=list(o.users.values())):
        assert not set(map(str, notice.values.values())) & ids
        assert notice.code in ("sight_narrowed", "sight_widened")

"""The pair table of duplicate letters (plan Task 1): one ordered row per pair, gone with
either letter, readable only for an actor who reads both letters (RLS), and the seam
`duplicates.visible_pairs` on top of `visibility.visible_documents`."""

import pytest
from django.db import IntegrityError, transaction

from apps.documents import duplicates
from apps.documents.models import Document, DocumentEvent, DuplicateCandidate
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import make_document, pair, trashed
from tests.visibility_support import Office, as_actor, office


def _ordered_pair(workspace: Workspace) -> tuple[Document, Document]:
    one, two = make_document(workspace), make_document(workspace)
    return (one, two) if one.id < two.id else (two, one)


def _membership(o: Office, name: str) -> Membership:
    return Membership.objects.get(workspace=o.workspace, user=o.users[name])


def _seer(o: Office, *letters: str) -> str:
    """The first Office user who sees all of `letters`."""
    return next(n for n, u in o.users.items() if set(letters) <= o.visible(u.id))


def test_pair_is_stored_ordered(ws_a: Workspace) -> None:
    a, b = _ordered_pair(ws_a)

    row = pair(b, a)

    assert (row.first_id, row.second_id) == (a.id, b.id)
    with as_actor(ws_a, SYSTEM), pytest.raises(IntegrityError), transaction.atomic():
        DuplicateCandidate.objects.filter(id=row.id).update(first=b, second=a)


def test_ordered_sorts_two_ids(ws_a: Workspace) -> None:
    a, b = _ordered_pair(ws_a)

    assert duplicates.ordered(b.id, a.id) == (a.id, b.id)
    assert duplicates.ordered(a.id, b.id) == (a.id, b.id)


def test_one_row_per_pair(ws_a: Workspace) -> None:
    a, b = _ordered_pair(ws_a)
    pair(a, b, state="dismissed")

    with pytest.raises(IntegrityError), transaction.atomic():
        pair(b, a)


def test_purge_of_a_letter_removes_the_pair(ws_a: Workspace) -> None:
    a, b = _ordered_pair(ws_a)
    pair(a, b)

    with as_actor(ws_a, SYSTEM):
        Document.objects.filter(id=b.id).delete()
        assert DuplicateCandidate.objects.count() == 0
        assert Document.objects.filter(id=a.id).exists()


def test_rls_needs_both_letters() -> None:
    o = office()
    pair(o.letters["private_femke"], o.letters["own_piet"])

    with as_actor(o.workspace, o.plain.id):
        assert DuplicateCandidate.objects.count() == 0
    for name, user in o.users.items():
        sees_both = {"private_femke", "own_piet"} <= o.visible(user.id)
        with as_actor(o.workspace, user.id):
            assert DuplicateCandidate.objects.count() == (1 if sees_both else 0), name
    with as_actor(o.workspace, SYSTEM):
        assert DuplicateCandidate.objects.count() == 1


def test_rls_refuses_update_by_one_sided_actor() -> None:
    o = office()
    row = pair(o.letters["private_femke"], o.letters["own_piet"])

    with as_actor(o.workspace, o.plain.id):
        assert DuplicateCandidate.objects.filter(id=row.id).update(state="dismissed") == 0
    with as_actor(o.workspace, SYSTEM):
        assert DuplicateCandidate.objects.get(id=row.id).state == "open"


def test_visible_pairs_follow_the_policies() -> None:
    o = office()
    names = [
        ("private_femke", "finance_letter"),
        ("own_piet", "unassigned"),
        ("hr_letter", "finance_letter"),
    ]
    rows = {pair(o.letters[x], o.letters[y]).id: {x, y} for x, y in names}

    sizes: set[int] = set()
    for state in ({"e": False, "d": True, "a": False}, {"e": True, "d": True, "a": True}):
        o.state(**state)
        for name, user in o.users.items():
            seen = o.visible(user.id)
            expected = {row_id for row_id, both in rows.items() if both <= seen}
            with as_actor(o.workspace, user.id):
                got = set(
                    duplicates.visible_pairs(_membership(o, name)).values_list("id", flat=True)
                )
            assert got == expected, (state, name)
            sizes.add(len(expected))
    # Some see none, some see a part, some see all three: the test is not vacuous.
    assert {0, 3} <= sizes
    assert len(sizes) >= 3


def test_visible_pairs_hide_trashed_letter() -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    row = pair(o.letters["own_piet"], o.letters["unassigned"])
    name = _seer(o, "own_piet", "unassigned")
    membership = _membership(o, name)

    trashed(o.letters["unassigned"])
    with as_actor(o.workspace, o.users[name].id):
        assert list(duplicates.visible_pairs(membership)) == []
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=o.letters["unassigned"].id).update(trashed_at=None)
    with as_actor(o.workspace, o.users[name].id):
        assert [p.id for p in duplicates.visible_pairs(membership)] == [row.id]


def test_visible_pairs_only_open() -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    pair(o.letters["own_piet"], o.letters["unassigned"], state="dismissed")
    pair(o.letters["hr_letter"], o.letters["unassigned"], state="merged")
    pair(o.letters["only_directie"], o.letters["unassigned"], state="withdrawn")
    shown = pair(o.letters["person_without_account"], o.letters["unassigned"])
    name = _seer(
        o, "own_piet", "hr_letter", "only_directie", "person_without_account", "unassigned"
    )

    with as_actor(o.workspace, o.users[name].id):
        got = [p.id for p in duplicates.visible_pairs(_membership(o, name))]

    assert got == [shown.id]


def test_visible_pairs_refuses_the_system(ws_a: Workspace) -> None:
    membership = Membership.objects.get(workspace=ws_a, role="admin")

    with as_actor(ws_a, SYSTEM), pytest.raises(RuntimeError):
        duplicates.visible_pairs(membership)


def test_duplicate_not_found_is_404() -> None:
    error = duplicates.duplicate_not_found()

    assert (error.status, error.code) == (404, "duplicate_not_found")
    assert error.message == "We kunnen deze dubbele brieven niet vinden."


def test_event_related_document_same_workspace(ws_a: Workspace, ws_b: Workspace) -> None:
    here = make_document(ws_a)
    there = make_document(ws_b)

    with as_actor(ws_a, SYSTEM), pytest.raises(ValueError, match="related_document"):
        DocumentEvent.objects.create(
            workspace=ws_a,
            document=here,
            kind=DocumentEvent.Kind.MERGED,
            origin=DocumentEvent.Origin.MERGE,
            related_document_id=there.id,
        )

"""Merge and dismiss of a duplicate pair (plan Task 4): the kept letter gets the other's
labels, addressees and open tasks, the other goes to the trash through `move_to_trash`;
refusals change nothing; a restore after a merge brings no second payment task."""

import uuid
from collections.abc import Callable, Collection
from datetime import date
from uuid import UUID

import pytest

from apps.accounts.models import User
from apps.audit.codes import ActorKind
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.documents import duplicates, organise, retention, services, trash, visibility
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DuplicateCandidate,
)
from apps.documents.retention import RemovalVerdict
from apps.labels.models import Label
from apps.people.models import Person
from apps.tasks import services as task_services
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, make_document, make_person, pair, trashed
from tests.members_support import workspace_rows
from tests.visibility_support import office, restrict_raw

pytestmark = pytest.mark.usefixtures("without_preset_rules")


def actor_of(user: User) -> AuditActor:
    return AuditActor(kind=ActorKind.USER, user_id=user.id, name=user.name)


def membership_of(ws: Workspace, user: User) -> Membership:
    return Membership.objects.get(workspace=ws, user=user)


def do_merge(
    ws: Workspace,
    candidate: DuplicateCandidate,
    keep: Document,
    *,
    user: User | None = None,
    complete_twin_tasks: bool = True,
) -> Document:
    who = user or admin_of(ws)
    with workspace_context(ws.id, actor=who.id):
        return duplicates.merge(
            membership=membership_of(ws, who),
            user=who,
            actor=actor_of(who),
            candidate_id=candidate.id,
            keep_id=keep.id,
            complete_twin_tasks=complete_twin_tasks,
        )


def do_dismiss(ws: Workspace, candidate: DuplicateCandidate, *, user: User | None = None) -> None:
    who = user or admin_of(ws)
    with workspace_context(ws.id, actor=who.id):
        duplicates.dismiss(membership=membership_of(ws, who), user=who, candidate_id=candidate.id)


def fresh(document: Document) -> Document:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Document.objects.get(id=document.id)


def fresh_pair(candidate: DuplicateCandidate) -> DuplicateCandidate:
    with workspace_context(candidate.workspace_id, actor=SYSTEM):
        return DuplicateCandidate.objects.get(id=candidate.id)


def events(document: Document, kind: str | None = None) -> list[DocumentEvent]:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        found = DocumentEvent.objects.filter(document_id=document.id).order_by("created_at", "id")
        return list(found if kind is None else found.filter(kind=kind))


def labels_of(document: Document) -> set[str]:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return set(
            DocumentLabel.objects.filter(document_id=document.id).values_list(
                "label__name", flat=True
            )
        )


def persons_of(document: Document) -> set[str]:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return set(
            DocumentAddressee.objects.filter(document_id=document.id).values_list(
                "person__display_name", flat=True
            )
        )


def tasks_of(document: Document) -> list[Task]:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return list(Task.objects.filter(document_id=document.id).order_by("created_at", "id"))


def label(ws: Workspace, name: str) -> Label:
    with workspace_context(ws.id, actor=SYSTEM):
        return Label.objects.create(workspace=ws, name=name)


def link(
    document: Document, *, labels: Collection[Label] = (), persons: Collection[Person] = ()
) -> None:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        for each in labels:
            DocumentLabel.objects.create(
                workspace_id=document.workspace_id, document=document, label=each
            )
        for person in persons:
            DocumentAddressee.objects.create(
                workspace_id=document.workspace_id, document=document, person=person
            )
        Document.objects.filter(id=document.id).update(
            addressee_ids=sorted(
                DocumentAddressee.objects.filter(document_id=document.id).values_list(
                    "person_id", flat=True
                )
            )
        )


def task(
    document: Document,
    *,
    status: str = Task.Status.OPEN,
    description: str = "Betalen",
    type: str = Task.Type.PAY,
    deadline: date | None = None,
    deadline_source: str = Task.DeadlineSource.MANUAL,
) -> Task:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Task.objects.create(
            workspace_id=document.workspace_id,
            document=document,
            type=type,
            description=description,
            status=status,
            deadline=deadline,
            deadline_source=deadline_source,
        )


def history(ws: Workspace, user: User, document: Document) -> list[DocumentEvent]:
    """The letter's history as `user` reads it (the API's read)."""
    with workspace_context(ws.id, actor=user.id):
        return services.list_document_events(
            membership=membership_of(ws, user), document_id=document.id
        )


def reads(ws: Workspace, user: User, document: Document) -> bool:
    """Whether `user` may read the letter, in the trash or not."""
    with workspace_context(ws.id, actor=user.id):
        return Document.objects.filter(id=document.id).exists()


def two_letters(ws: Workspace) -> tuple[Document, Document, DuplicateCandidate]:
    keep, other = make_document(ws, "done"), make_document(ws, "done")
    return keep, other, pair(keep, other)


def refused(code: str, call: Callable[[], object]) -> None:
    with pytest.raises(ApiError) as caught:
        call()
    assert caught.value.code == code


def test_merge_unites_labels_and_addressees(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    l1, l2 = label(ws_a, "Eerste"), label(ws_a, "Tweede")
    p1, p2 = make_person(ws_a, "Jan"), make_person(ws_a, "Mies")
    link(keep, labels=[l1], persons=[p1])
    link(other, labels=[l1, l2], persons=[p2])
    admin = admin_of(ws_a)

    do_merge(ws_a, candidate, keep)

    assert labels_of(keep) == {"Eerste", "Tweede"}
    assert persons_of(keep) == {"Jan", "Mies"}
    [added_label] = events(keep, DocumentEvent.Kind.LABEL_ADDED)
    [added_person] = events(keep, DocumentEvent.Kind.ADDRESSEE_ADDED)
    for event in (added_label, added_person):
        assert (event.origin, event.actor_id) == ("merge", admin.id)
        assert event.origin not in organise.UNDOABLE_ORIGINS
    assert added_label.label_id == l2.id
    assert added_person.person_id == p2.id
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert not DocumentLabel.objects.filter(document=keep, automatic=True).exists()
        assert not DocumentAddressee.objects.filter(document=keep, automatic=True).exists()
    assert set(fresh(keep).addressee_ids) == {p1.id, p2.id}


def test_merge_puts_other_in_trash(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    admin = admin_of(ws_a)

    kept = do_merge(ws_a, candidate, keep)

    assert kept.id == keep.id
    after = fresh(other)
    assert after.trashed_at is not None
    assert after.trashed_by_id == admin.id
    assert fresh(keep).trashed_at is None
    [row] = workspace_rows(ws_a, "document_trashed")
    assert row.values == {"reason": "duplicate"}
    assert (row.target_id, row.actor_id) == (other.id, admin.id)
    decided = fresh_pair(candidate)
    assert decided.state == DuplicateCandidate.State.MERGED
    assert decided.kept_id == keep.id
    assert decided.decided_by_id == admin.id
    assert decided.decided_at is not None


def test_merge_history_on_both(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    admin = admin_of(ws_a)

    do_merge(ws_a, candidate, keep)

    [merged] = events(keep, DocumentEvent.Kind.MERGED)
    assert (merged.origin, merged.actor_id, merged.related_document_id) == (
        "user",
        admin.id,
        other.id,
    )
    [trashed] = events(other, DocumentEvent.Kind.TRASHED)
    assert (trashed.origin, trashed.actor_id, trashed.related_document_id) == (
        "merge",
        admin.id,
        keep.id,
    )


def test_merge_moves_open_tasks_once(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    moving = task(other, description="Betalen aan de gemeente", deadline=date(2026, 12, 1))
    task(keep)
    staying = task(other)

    do_merge(ws_a, candidate, keep)

    on_keep = tasks_of(keep)
    assert {t.id for t in on_keep} >= {moving.id}
    assert [t.deadline for t in on_keep].count(None) == 1
    assert [t.id for t in tasks_of(other)] == [staying.id]
    for document, related in ((keep, other), (other, keep)):
        [moved] = events(document, DocumentEvent.Kind.TASK_MOVED)
        assert (moved.origin, moved.related_document_id) == ("merge", related.id)


def test_merge_completes_twin_when_asked(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    twin = task(keep)
    task(other, status=Task.Status.DONE)
    admin = admin_of(ws_a)

    do_merge(ws_a, candidate, keep, complete_twin_tasks=True)

    [after] = [t for t in tasks_of(keep) if t.id == twin.id]
    assert after.status == Task.Status.DONE
    assert after.completed_at is not None
    [completed] = events(keep, DocumentEvent.Kind.TASK_COMPLETED)
    assert (completed.origin, completed.actor_id) == ("merge", admin.id)


def test_merge_completes_twin_through_the_task_service(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One place completes a task: the merge calls `tasks.services.complete_task`."""
    keep, other, candidate = two_letters(ws_a)
    twin = task(keep)
    task(other, status=Task.Status.DONE)
    calls: list[tuple[UUID, str]] = []
    real = task_services.complete_task

    def spy(*, workspace: Workspace, user: User, task_id: UUID, origin: str) -> Task:
        calls.append((task_id, origin))
        return real(workspace=workspace, user=user, task_id=task_id, origin=origin)

    monkeypatch.setattr(task_services, "complete_task", spy)

    do_merge(ws_a, candidate, keep, complete_twin_tasks=True)

    assert calls == [(twin.id, DocumentEvent.Origin.MERGE)]


def test_merge_leaves_twin_open_when_not_asked(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    twin = task(keep)
    task(other, status=Task.Status.DONE)

    do_merge(ws_a, candidate, keep, complete_twin_tasks=False)

    [after] = [t for t in tasks_of(keep) if t.id == twin.id]
    assert after.status == Task.Status.OPEN
    assert events(keep, DocumentEvent.Kind.TASK_COMPLETED) == []


def test_merge_completes_only_a_twin_of_the_kept_letter(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    moving = task(other)
    task(other, status=Task.Status.DONE)

    do_merge(ws_a, candidate, keep, complete_twin_tasks=True)

    [after] = [t for t in tasks_of(keep) if t.id == moving.id]
    assert after.status == Task.Status.OPEN
    assert events(keep, DocumentEvent.Kind.TASK_COMPLETED) == []


def test_task_twins_both_directions(ws_a: Workspace) -> None:
    first, second, _ = two_letters(ws_a)
    task(first, status=Task.Status.DONE)
    task(second)
    task(second, status=Task.Status.DONE, description="Reageren", type=Task.Type.RESPOND)
    task(first, description="Reageren", type=Task.Type.RESPOND)

    with workspace_context(ws_a.id, actor=SYSTEM):
        twins = duplicates.task_twins(first, second)

    assert {(t.done_document_id, t.open_document_id, t.description) for t in twins} == {
        (first.id, second.id, "Betalen"),
        (second.id, first.id, "Reageren"),
    }


def _unchanged(candidate: DuplicateCandidate, *letters: Document) -> None:
    assert fresh_pair(candidate).state == DuplicateCandidate.State.OPEN
    for document in letters:
        assert fresh(document).trashed_at is None
        assert events(document) == []


def test_merge_refuses_different_viewers() -> None:
    o = office()
    private, finance = o.letters["private_femke"], o.letters["finance_letter"]
    candidate = pair(private, finance)
    before = {d.id: (labels_of(d), persons_of(d)) for d in (private, finance)}

    refused(
        "duplicate_visibility_differs",
        lambda: do_merge(o.workspace, candidate, finance, user=o.finance),
    )

    assert fresh_pair(candidate).state == DuplicateCandidate.State.OPEN
    for document in (private, finance):
        assert fresh(document).trashed_at is None
        assert (labels_of(document), persons_of(document)) == before[document.id]
    assert workspace_rows(o.workspace, "document_trashed") == []


def test_merge_refuses_when_union_widens_viewers(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    keep, other, candidate = two_letters(ws_a)
    l2 = label(ws_a, "Tweede")
    p2 = make_person(ws_a, "Mies")
    link(other, labels=[l2], persons=[p2])
    real = visibility.viewers
    stranger = uuid.uuid4()

    def widened(document_ids: Collection[UUID]) -> dict[UUID, frozenset[UUID]]:
        seen = real(document_ids)
        if p2.id in fresh(keep).addressee_ids:
            seen[keep.id] = seen[keep.id] | {stranger}
        return seen

    monkeypatch.setattr(visibility, "viewers", widened)

    refused("duplicate_visibility_differs", lambda: do_merge(ws_a, candidate, keep))

    assert labels_of(keep) == set()
    assert persons_of(keep) == set()
    assert fresh(other).trashed_at is None
    assert fresh_pair(candidate).state == DuplicateCandidate.State.OPEN


def test_merge_refuses_processing(ws_a: Workspace) -> None:
    keep = make_document(ws_a, "done")
    other = make_document(ws_a, "processing")
    candidate = pair(keep, other)

    refused("document_processing", lambda: do_merge(ws_a, candidate, keep))

    _unchanged(candidate, keep, other)


def test_merge_refuses_keep_outside_pair(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    third = make_document(ws_a, "done")

    refused("validation_error", lambda: do_merge(ws_a, candidate, third))

    _unchanged(candidate, keep, other)


def test_merge_not_open_409(ws_a: Workspace) -> None:
    keep, other = make_document(ws_a, "done"), make_document(ws_a, "done")
    candidate = pair(keep, other, state=DuplicateCandidate.State.DISMISSED)

    refused("duplicate_not_open", lambda: do_merge(ws_a, candidate, keep))
    refused("duplicate_not_open", lambda: do_dismiss(ws_a, candidate))

    assert fresh(other).trashed_at is None


def test_merge_unseen_pair_404() -> None:
    o = office()
    private, own = o.letters["private_femke"], o.letters["own_piet"]
    candidate = pair(private, own)

    refused("duplicate_not_found", lambda: do_merge(o.workspace, candidate, own, user=o.plain))
    refused("duplicate_not_found", lambda: do_dismiss(o.workspace, candidate, user=o.plain))
    random_id = DuplicateCandidate(id=uuid.uuid4(), workspace_id=o.workspace.id)
    refused("duplicate_not_found", lambda: do_merge(o.workspace, random_id, own, user=o.plain))

    assert fresh_pair(candidate).state == DuplicateCandidate.State.OPEN


def test_merge_refused_by_retention_hook(ws_a: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    keep, other, candidate = two_letters(ws_a)
    l2 = label(ws_a, "Tweede")
    link(other, labels=[l2])
    monkeypatch.setattr(
        retention,
        "check_removal",
        lambda document, *, step: RemovalVerdict(allowed=False, warning="x"),
    )

    refused("document_retained", lambda: do_merge(ws_a, candidate, keep))

    assert labels_of(keep) == set()
    assert fresh(other).trashed_at is None
    assert fresh_pair(candidate).state == DuplicateCandidate.State.OPEN
    assert events(keep) == []


def test_restore_after_merge_is_safe(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    link(other, labels=[label(ws_a, "Tweede")])
    moving = task(other)
    admin = admin_of(ws_a)
    do_merge(ws_a, candidate, keep)

    with workspace_context(ws_a.id, actor=admin.id):
        trash.restore_document(
            membership=membership_of(ws_a, admin),
            user=admin,
            actor=actor_of(admin),
            document_id=other.id,
        )

    assert fresh(other).trashed_at is None
    assert fresh_pair(candidate).state == DuplicateCandidate.State.MERGED
    with workspace_context(ws_a.id, actor=admin.id):
        assert not duplicates.visible_pairs(membership_of(ws_a, admin)).exists()
    assert "Tweede" in labels_of(keep)
    assert moving.id in {t.id for t in tasks_of(keep)}
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert (
            Task.objects.filter(
                document_id__in=[keep.id, other.id], type=Task.Type.PAY, status=Task.Status.OPEN
            ).count()
            == 1
        )


def test_dismiss_records_on_both(ws_a: Workspace) -> None:
    first, second, candidate = two_letters(ws_a)
    admin = admin_of(ws_a)

    do_dismiss(ws_a, candidate)

    decided = fresh_pair(candidate)
    assert decided.state == DuplicateCandidate.State.DISMISSED
    assert decided.decided_by_id == admin.id
    assert decided.kept_id is None
    for document, related in ((first, second), (second, first)):
        [event] = events(document, DocumentEvent.Kind.DUPLICATE_DISMISSED)
        assert (event.origin, event.actor_id, event.related_document_id) == (
            "user",
            admin.id,
            related.id,
        )
    assert workspace_rows(ws_a, "document_trashed") == []
    assert fresh(first).trashed_at is None
    assert fresh(second).trashed_at is None


def test_move_to_trash_without_user_refuses(ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    admin = admin_of(ws_a)

    with (
        workspace_context(ws_a.id, actor=SYSTEM),
        pytest.raises(ValueError, match="needs the person"),
    ):
        trash.move_to_trash(document, user=None, actor=actor_of(admin))

    assert fresh(document).trashed_at is None


def test_plain_trash_history_unchanged(ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    admin = admin_of(ws_a)

    with workspace_context(ws_a.id, actor=admin.id):
        trash.trash_document(
            membership=membership_of(ws_a, admin),
            user=admin,
            actor=actor_of(admin),
            document_id=document.id,
        )

    [event] = events(document, DocumentEvent.Kind.TRASHED)
    assert (event.origin, event.actor_id, event.related_document_id) == (
        "user",
        admin.id,
        None,
    )


DUE = date(2026, 12, 15)


def test_merge_treats_differently_worded_payment_tasks_as_one(ws_a: Workspace) -> None:
    """Two AI runs word the same payment differently: one open payment task after the merge."""
    keep, other, candidate = two_letters(ws_a)
    task(
        keep,
        description="Betaal factuur 2026-118",
        deadline=DUE,
        deadline_source=Task.DeadlineSource.IN_DOCUMENT,
    )
    staying = task(
        other,
        description="Factuur 2026-118 betalen",
        deadline=DUE,
        deadline_source=Task.DeadlineSource.CALCULATED,
    )

    do_merge(ws_a, candidate, keep)

    open_pay = [
        t for t in tasks_of(keep) if t.type == Task.Type.PAY and t.status == Task.Status.OPEN
    ]
    assert len(open_pay) == 1
    assert [t.id for t in tasks_of(other)] == [staying.id]
    assert events(keep, DocumentEvent.Kind.TASK_MOVED) == []


def test_twin_ignores_wording_and_source(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    twin = task(keep, description="Betaal factuur 2026-118", deadline=DUE)
    task(
        other,
        status=Task.Status.DONE,
        description="Factuur 2026-118 betalen",
        deadline=DUE,
        deadline_source=Task.DeadlineSource.IN_DOCUMENT,
    )
    with workspace_context(ws_a.id, actor=SYSTEM):
        twins = duplicates.task_twins(keep, other)
    assert [(t.done_document_id, t.open_document_id) for t in twins] == [(other.id, keep.id)]

    do_merge(ws_a, candidate, keep, complete_twin_tasks=True)

    [after] = [t for t in tasks_of(keep) if t.id == twin.id]
    assert after.status == Task.Status.DONE


def test_tasks_with_different_deadlines_stay_two(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    task(keep, description="Betaal termijn 1", deadline=DUE)
    moving = task(other, description="Betaal termijn 2", deadline=date(2027, 1, 15))
    task(other, status=Task.Status.DONE, description="Betaal termijn 2", deadline=date(2027, 1, 15))
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert duplicates.task_twins(keep, other) == []

    do_merge(ws_a, candidate, keep)

    on_keep = tasks_of(keep)
    assert moving.id in {t.id for t in on_keep}
    assert sum(t.status == Task.Status.OPEN for t in on_keep) == 2


def test_tasks_of_another_type_stay_two(ws_a: Workspace) -> None:
    keep, other, candidate = two_letters(ws_a)
    task(keep, deadline=DUE)
    moving = task(other, type=Task.Type.OBJECT, description="Bezwaar maken", deadline=DUE)

    do_merge(ws_a, candidate, keep)

    assert moving.id in {t.id for t in tasks_of(keep)}


def test_history_hides_a_dismissed_letter_the_reader_cannot_read() -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    private, finance = o.letters["private_femke"], o.letters["finance_letter"]
    candidate = pair(private, finance)
    assert reads(o.workspace, o.plain, finance)
    assert not reads(o.workspace, o.plain, private)

    do_dismiss(o.workspace, candidate, user=o.finance)

    seen = history(o.workspace, o.plain, finance)
    assert all(e.kind != DocumentEvent.Kind.DUPLICATE_DISMISSED for e in seen)
    assert all(e.related_document_id in (None, finance.id) for e in seen)
    [own] = [
        e
        for e in history(o.workspace, o.finance, finance)
        if e.kind == DocumentEvent.Kind.DUPLICATE_DISMISSED
    ]
    assert own.related_document_id == private.id


def test_history_hides_the_merged_letter_once_the_reader_cannot_read_it() -> None:
    o = office()
    o.state(e=True, d=True, a=False)
    keep = o.letters["finance_letter"]
    other = make_document(o.workspace, "done")
    link(other, persons=[o.persons["Finance"]])
    task(other, deadline=DUE)
    candidate = pair(keep, other)
    do_merge(o.workspace, candidate, keep, user=o.finance)
    # Later the trashed scan becomes private to Femke.
    restrict_raw(other, [o.finance])
    assert reads(o.workspace, o.plain, keep)
    assert not reads(o.workspace, o.plain, other)

    seen = history(o.workspace, o.plain, keep)
    kinds = {e.kind for e in seen}
    assert {DocumentEvent.Kind.MERGED, DocumentEvent.Kind.TASK_MOVED} <= kinds
    assert all(e.related_document_id is None for e in seen)
    # Femke still reads the scan in the trash, so her history keeps the link.
    mine = history(o.workspace, o.finance, keep)
    assert {e.related_document_id for e in mine if e.kind == DocumentEvent.Kind.MERGED} == {
        other.id
    }


def test_a_pair_whose_letter_left_meanwhile_is_skipped(ws_a: Workspace) -> None:
    """Review M1: pairs and letters are read in two statements; a letter trashed in
    between leaves the pair out instead of a 500."""
    _, second, candidate = two_letters(ws_a)
    trashed(second)
    admin = admin_of(ws_a)

    with workspace_context(ws_a.id, actor=admin.id):
        membership = membership_of(ws_a, admin)
        assert duplicates._outs(membership, [candidate]) == []
        refused("duplicate_not_found", lambda: duplicates.duplicate_out(membership, candidate))

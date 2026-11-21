import uuid

import pytest

from apps.common.errors import ApiError
from apps.documents import organise, services, visibility
from apps.documents.models import Document, DocumentAddressee, DocumentEvent
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, make_document, make_person


def addressees(workspace: Workspace, document: Document) -> set[uuid.UUID]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return set(
            DocumentAddressee.objects.filter(document=document).values_list("person_id", flat=True)
        )


def events(workspace: Workspace, document: Document) -> list[tuple[str, str]]:
    """(kind, person_name) of the document's events, oldest first."""
    with workspace_context(workspace.id, actor=SYSTEM):
        return [
            (e.kind, e.person_name)
            for e in DocumentEvent.objects.filter(document=document).order_by("created_at", "id")
        ]


def set_addressees(workspace: Workspace, document: Document, ids: list[uuid.UUID]) -> None:
    with workspace_context(workspace.id, actor=SYSTEM):
        locked = Document.objects.select_for_update().get(id=document.id)
        with visibility.addressee_scope(locked):
            organise.set_addressees(document=locked, person_ids=ids, actor=admin_of(workspace))


def test_set_addressees_makes_the_set_exact_and_writes_events(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    a, b, c = (make_person(ws_a, name) for name in ("Anna", "Bert", "Cor"))

    set_addressees(ws_a, document, [a.id, b.id])
    set_addressees(ws_a, document, [b.id, c.id])

    assert addressees(ws_a, document) == {b.id, c.id}
    assert events(ws_a, document) == [
        ("addressee_added", "Anna"),
        ("addressee_added", "Bert"),
        ("addressee_removed", "Anna"),
        ("addressee_added", "Cor"),
    ]
    with workspace_context(ws_a.id, actor=SYSTEM):
        event = DocumentEvent.objects.filter(document=document).first()
        assert event is not None
        assert event.origin == "user"
        assert event.actor == admin_of(ws_a)


def test_same_set_again_writes_no_event(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    a = make_person(ws_a, "Anna")
    set_addressees(ws_a, document, [a.id])

    set_addressees(ws_a, document, [a.id, a.id])

    assert events(ws_a, document) == [("addressee_added", "Anna")]


def test_addressee_of_another_workspace_is_404(ws_a: Workspace, ws_b: Workspace) -> None:
    document = make_document(ws_a)
    a = make_person(ws_a, "Anna")
    stranger = make_person(ws_b, "Vreemde")
    set_addressees(ws_a, document, [a.id])

    with pytest.raises(ApiError) as excinfo:
        set_addressees(ws_a, document, [stranger.id])

    assert excinfo.value.status == 404
    assert excinfo.value.code == "person_not_found"
    assert addressees(ws_a, document) == {a.id}
    assert len(events(ws_a, document)) == 1


def test_archived_person_cannot_be_added_but_stays(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    a = make_person(ws_a, "Anna")
    old = make_person(ws_a, "Oud", archived=True)
    set_addressees(ws_a, document, [a.id])
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentAddressee.objects.create(workspace=ws_a, document=document, person=old)

    with pytest.raises(ApiError) as excinfo:
        set_addressees(ws_a, document, [a.id, make_person(ws_a, "Ouder", archived=True).id])
    assert excinfo.value.status == 422
    assert excinfo.value.code == "validation_error"

    # Already on the document: keeping the archived person is fine.
    set_addressees(ws_a, document, [old.id])
    assert addressees(ws_a, document) == {old.id}


def test_add_addressee_reports_whether_it_added(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    a = make_person(ws_a, "Anna")
    with workspace_context(ws_a.id, actor=SYSTEM), visibility.addressee_scope(document):
        assert organise.add_addressee(document, a, actor=None, origin="rule") is True
        assert organise.add_addressee(document, a, actor=None, origin="rule") is False
        (event,) = DocumentEvent.objects.filter(document=document)
        assert event.origin == "rule"
        assert event.actor is None


@pytest.mark.parametrize(
    ("status", "with_addressee", "state"),
    [
        ("processing", False, "new"),
        ("needs_review", False, "review"),
        ("failed", True, "review"),
        ("done", False, "unassigned"),
        ("done", True, ""),
    ],
)
def test_inbox_states(ws_a: Workspace, status: str, with_addressee: bool, state: str) -> None:
    document = make_document(ws_a, status)
    if with_addressee:
        with workspace_context(ws_a.id, actor=SYSTEM):
            DocumentAddressee.objects.create(
                workspace=ws_a, document=document, person=make_person(ws_a, "Anna")
            )

    with workspace_context(ws_a.id, actor=SYSTEM):
        assert services.inbox_state(Document.objects.get(id=document.id)) == state


def test_list_filters_and_counts(ws_a: Workspace) -> None:
    papa = make_person(ws_a, "Papa")
    new = make_document(ws_a, "processing")
    review = make_document(ws_a, "needs_review")
    unassigned = make_document(ws_a, "done")
    sorted_ = make_document(ws_a, "done")
    membership = Membership.objects.get(workspace=ws_a, role="admin")
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentAddressee.objects.create(workspace=ws_a, document=sorted_, person=papa)
        DocumentAddressee.objects.create(workspace=ws_a, document=review, person=papa)
        # Links made by hand: `addressee_ids` follows them as the services keep it.
        visibility.sync_addressee_ids(sorted_)
        visibility.sync_addressee_ids(review)
    with workspace_context(ws_a.id, actor=membership.user_id):

        def ids(inbox_state: str = "", person_id: uuid.UUID | None = None) -> set[uuid.UUID]:
            found = services.list_documents(
                membership, inbox_state=inbox_state, person_id=person_id
            )
            return {d.id for d in found}

        assert ids() == {new.id, review.id, unassigned.id, sorted_.id}
        assert ids(inbox_state="new") == {new.id}
        assert ids(inbox_state="review") == {review.id}
        assert ids(inbox_state="unassigned") == {unassigned.id}
        assert ids(person_id=papa.id) == {review.id, sorted_.id}
        assert ids(inbox_state="review", person_id=papa.id) == {review.id}
        assert services.inbox_counts(membership) == {
            "new": 1,
            "review": 1,
            "unassigned": 1,
            "automatic": 0,
            "duplicate": 0,
        }

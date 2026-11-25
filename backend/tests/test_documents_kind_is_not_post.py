"""An own document is not post that waits (archive plan Task 2, spec S 8, AR7, AR8): not in
the Inbox, never unread, never "Blijft liggen", not in Mijn post, no letter status."""

import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.common.errors import ApiError
from apps.documents import handle, lingering, reading, services
from apps.documents.models import Document
from apps.tasks import letter_sync
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.archive_support import membership_of, own_document
from tests.my_tasks_support import household
from tests.visibility_support import as_actor, letter, office


def _ids(documents: list[Document]) -> set[uuid.UUID]:
    return {d.id for d in documents}


def test_own_document_is_never_in_the_inbox_lists_or_counts() -> None:
    h = household()
    nameless_letter = letter(h.workspace, on=[])
    nameless_document = own_document(h.workspace, on=[], scanned_by=h.anne)
    processing = own_document(h.workspace, on=[], scanned_by=h.anne, status="processing")
    member = membership_of(h.workspace, h.anne)
    with as_actor(h.workspace, h.anne.id):
        unassigned = _ids(services.list_documents(member, inbox_state="unassigned"))
        new = _ids(services.list_documents(member, inbox_state="new"))
        counts = services.inbox_counts(member)
        waiting = services.waiting_letters(member)
        stored = Document.objects.get(id=nameless_document.id)
    assert nameless_letter.id in unassigned
    assert nameless_document.id not in unassigned
    assert processing.id not in new
    assert counts["unassigned"] == 1
    assert counts["new"] == 0
    assert waiting == 1
    assert services.inbox_state(stored) == ""


def test_own_document_is_never_unread_for_anyone() -> None:
    h = household()
    post = letter(h.workspace, on=[h.persons["Piet"]])
    document = own_document(h.workspace, on=[h.persons["Piet"]], scanned_by=h.anne)
    member = membership_of(h.workspace, h.piet)
    with as_actor(h.workspace, h.piet.id):
        rows = services.list_documents(member)
        unread = reading.unread_ids(member, rows)
        only_unread = _ids(services.list_documents(member, unread_only=True))
        menu = services.menu_counts(member)
        detail = services.document_out(Document.objects.get(id=document.id))
    assert post.id in unread
    assert document.id not in unread
    assert document.id not in only_unread
    assert menu.my_post_unread == 1
    assert detail.unread is False


def test_own_document_is_never_lingering() -> None:
    o = office()
    then = timezone.now() - timedelta(days=30)
    nameless = own_document(o.workspace, on=[], scanned_by=o.plain)
    addressed = own_document(o.workspace, on=[o.persons["Jan"]], scanned_by=o.plain)
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id__in=[nameless.id, addressed.id]).update(
            created_at=then, confirmed_at=then
        )
    for user in (o.admin, o.sorter):
        member = membership_of(o.workspace, user)
        with as_actor(o.workspace, user.id):
            result = lingering.lingering_letters(member)
        listed = _ids(result.too_long_in_inbox) | _ids(result.not_opened)
        assert nameless.id not in listed
        assert addressed.id not in listed


def test_mijn_post_lists_letters_only() -> None:
    h = household()
    post = letter(h.workspace, on=[h.persons["Piet"]])
    document = own_document(h.workspace, on=[h.persons["Piet"]], scanned_by=h.piet)
    member = membership_of(h.workspace, h.piet)
    with as_actor(h.workspace, h.piet.id):
        mine = _ids(services.list_documents(member, addressed_to_me=True))
        everything = _ids(services.list_documents(member))
    assert post.id in mine
    assert document.id not in mine
    assert document.id in everything


def test_opening_an_own_document_sets_no_addressee_opened_at() -> None:
    h = household()
    document = own_document(h.workspace, on=[h.persons["Piet"]], scanned_by=h.anne)
    member = membership_of(h.workspace, h.piet)
    with as_actor(h.workspace, h.piet.id):
        reading.mark_read(member, document.id)
    with as_actor(h.workspace, SYSTEM):
        stored = Document.objects.get(id=document.id)
        reading.recompute_addressee_opened(stored)
        again = Document.objects.get(id=document.id)
    assert stored.addressee_opened_at is None
    assert again.addressee_opened_at is None


@pytest.mark.parametrize(
    "name",
    ["documents_document_unassigned", "documents_document_unopened", "documents_document_opened"],
)
def test_the_three_partial_indexes_have_kind_in_their_condition(name: str) -> None:
    with connection.cursor() as c:
        c.execute("SELECT indexdef FROM pg_indexes WHERE indexname = %s", [name])
        row = c.fetchone()
    assert row is not None
    assert "((kind)::text = 'letter'::text)" in row[0]


def test_own_document_has_no_letter_status_and_is_not_counted() -> None:
    h = household()
    letter(h.workspace, on=[h.persons["Anne"]])
    document = own_document(h.workspace, on=[h.persons["Anne"]], scanned_by=h.anne)
    member = membership_of(h.workspace, h.anne)
    with as_actor(h.workspace, h.anne.id):
        counts = services.status_counts(member)
        handled = _ids(services.list_documents(member, letter_status="handled"))
        row = services.document_summaries_out(member, [Document.objects.get(id=document.id)])[0]
    # The letter's raw task is not synced: it counts as handled; the document not at all.
    assert (counts.new, counts.open, counts.waiting, counts.handled) == (0, 0, 0, 1)
    assert document.id not in handled
    assert (row.letter_status, row.overdue, row.open_task_count) == ("", False, 0)


def test_an_open_task_on_an_own_document_leaves_its_facts_at_zero() -> None:
    h = household()
    document = own_document(h.workspace, on=[h.persons["Anne"]], scanned_by=h.anne)
    with as_actor(h.workspace, SYSTEM):
        Task.objects.create(
            workspace=h.workspace,
            document=document,
            type="other",
            description="Geldig tot",
            deadline=timezone.localdate() - timedelta(days=1),
        )
        locked = Document.objects.get(id=document.id)
        letter_sync.sync(locked)
        assert letter_sync.verify_workspace(h.workspace.id) == 0
        stored = Document.objects.get(id=document.id)
    assert (stored.task_open_count, stored.task_wait_until, stored.task_overdue_from) == (
        0,
        None,
        None,
    )


def test_an_own_document_is_not_handled_as_a_letter() -> None:
    h = household()
    document = own_document(h.workspace, on=[h.persons["Anne"]], scanned_by=h.anne)
    member = membership_of(h.workspace, h.anne)
    with as_actor(h.workspace, h.anne.id), pytest.raises(ApiError) as caught:
        handle.handle_document(
            membership=member,
            user=h.anne,
            document_id=document.id,
            task_ids=[],
            payments_confirmed=False,
        )
    assert (caught.value.status, caught.value.code) == (409, "document_not_a_letter")

"""Moving a letter to the trash and back (services): the fields, the history line and
the audit row, and nothing else about the letter changes."""

import uuid
from collections.abc import Callable

import pytest
from django.test import Client
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.accounts.models import User
from apps.audit.codes import ActorKind
from apps.audit.services import AuditActor
from apps.common.errors import ApiError
from apps.documents import trash
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.labels.models import Label
from apps.pipeline.jobs import defer_processing
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, documents_url, make_document, make_person, trashed
from tests.members_support import workspace_rows

SENDER = "Belastingdienst"
SUMMARY = "Je moet de aanslag inkomstenbelasting betalen."


def actor_of(user: User) -> AuditActor:
    return AuditActor(kind=ActorKind.USER, user_id=user.id, name=user.name)


def membership_of(ws: Workspace, user: User) -> Membership:
    return Membership.objects.get(workspace=ws, user=user)


def do_trash(ws: Workspace, document: Document) -> Document:
    user = admin_of(ws)
    with workspace_context(ws.id, actor=user.id):
        return trash.trash_document(
            membership=membership_of(ws, user),
            user=user,
            actor=actor_of(user),
            document_id=document.id,
        )


def do_restore(ws: Workspace, document: Document) -> Document:
    user = admin_of(ws)
    with workspace_context(ws.id, actor=user.id):
        return trash.restore_document(
            membership=membership_of(ws, user),
            user=user,
            actor=actor_of(user),
            document_id=document.id,
        )


def fresh(document: Document) -> Document:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return Document.objects.get(id=document.id)


def events(document: Document, kind: str) -> list[DocumentEvent]:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return list(DocumentEvent.objects.filter(document_id=document.id, kind=kind))


def jobs_for(document: Document) -> int:
    return ProcrastinateJob.objects.filter(
        task_name="process_document", args__document_id=str(document.id)
    ).count()


def organised_letter(ws: Workspace) -> Document:
    document = make_document(ws, "done")
    person = make_person(ws, "Oma")
    with workspace_context(ws.id, actor=SYSTEM):
        label = Label.objects.create(workspace=ws, name="Zwaluwstaart")
        other = Label.objects.create(workspace=ws, name="Kikkerpoel")
        DocumentAddressee.objects.create(workspace=ws, document=document, person=person)
        DocumentLabel.objects.create(workspace=ws, document=document, label=label)
        DocumentSuggestion.objects.create(
            workspace=ws,
            document=document,
            action=DocumentSuggestion.Action.ADD_LABEL,
            label=other,
        )
        Task.objects.create(workspace=ws, document=document, type="pay", description="Betaal")
        Document.objects.filter(id=document.id).update(
            sender=SENDER, summary=SUMMARY, addressee_ids=[person.id]
        )
    return fresh(document)


def snapshot(document: Document) -> tuple[object, ...]:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        d = Document.objects.get(id=document.id)
        return (
            d.status,
            d.sender,
            d.summary,
            d.addressee_ids,
            d.has_automatic_link,
            d.visibility,
            sorted(DocumentLabel.objects.filter(document=d).values_list("label_id", "automatic")),
            sorted(
                DocumentAddressee.objects.filter(document=d).values_list("person_id", "automatic")
            ),
            sorted(DocumentSuggestion.objects.filter(document=d).values_list("id", "state")),
            sorted(Task.objects.filter(document=d).values_list("id", "status")),
        )


def test_trash_sets_fields_event_and_audit(ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    admin = admin_of(ws_a)

    do_trash(ws_a, document)

    after = fresh(document)
    assert after.trashed_at is not None
    assert after.trashed_by_id == admin.id
    assert after.purge_started_at is None
    [event] = events(document, "trashed")
    assert (event.actor_id, event.origin) == (admin.id, "user")
    [row] = workspace_rows(ws_a, "document_trashed")
    assert (row.target_kind, row.target_id, row.target_name) == ("document", document.id, "")
    assert row.values == {"reason": "user"}
    assert (row.actor_kind, row.actor_id) == ("user", admin.id)


def test_trash_changes_nothing_else(ws_a: Workspace) -> None:
    document = organised_letter(ws_a)
    before = snapshot(document)

    do_trash(ws_a, document)

    assert snapshot(document) == before


def test_trash_twice_is_idempotent(ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")

    first = do_trash(ws_a, document)
    second = do_trash(ws_a, document)

    assert second.id == document.id
    assert second.trashed_at == first.trashed_at
    assert len(events(document, "trashed")) == 1
    assert len(workspace_rows(ws_a, "document_trashed")) == 1


def test_restore_clears_fields_and_records(ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")
    do_trash(ws_a, document)
    admin = admin_of(ws_a)

    restored = do_restore(ws_a, document)

    after = fresh(document)
    assert (restored.trashed_at, after.trashed_at, after.trashed_by_id) == (None, None, None)
    [event] = events(document, "restored")
    assert event.actor_id == admin.id
    [row] = workspace_rows(ws_a, "document_restored")
    assert (row.target_kind, row.target_id, row.values) == ("document", document.id, {})


def test_restore_of_live_letter_changes_nothing(ws_a: Workspace) -> None:
    document = make_document(ws_a, "done")

    assert do_restore(ws_a, document).id == document.id

    assert events(document, "restored") == []
    assert workspace_rows(ws_a, "document_restored") == []


def test_restore_processing_letter_requeues(ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a, "processing"))

    do_restore(ws_a, document)

    assert jobs_for(document) == 1


def test_restore_processing_with_live_job_does_not_double(ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a, "processing"))
    defer_processing(workspace_id=ws_a.id, document_id=document.id)

    do_restore(ws_a, document)

    assert jobs_for(document) == 1


def test_restore_of_done_letter_queues_nothing(ws_a: Workspace) -> None:
    document = trashed(make_document(ws_a, "done"))

    do_restore(ws_a, document)

    assert jobs_for(document) == 0


def test_audit_row_holds_no_content(ws_a: Workspace) -> None:
    document = organised_letter(ws_a)

    do_trash(ws_a, document)
    do_restore(ws_a, document)

    rows = [
        *workspace_rows(ws_a, "document_trashed"),
        *workspace_rows(ws_a, "document_restored"),
    ]
    assert len(rows) == 2
    for row in rows:
        for column in (str(row.values), row.target_name, row.actor_name):
            assert SENDER not in column
            assert "aanslag" not in column
            assert "Zwaluwstaart" not in column


def test_restored_letter_is_back_everywhere(client: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "processing")
    with workspace_context(ws_a.id, actor=SYSTEM):
        task = Task.objects.create(workspace=ws_a, document=document, type="pay", description="x")
    client.force_login(admin_of(ws_a))

    def seen() -> tuple[list[str], list[str], int]:
        listed = [row["id"] for row in client.get(documents_url(ws_a.id)).json()]
        tasks = [row["id"] for row in client.get(f"/api/v1/workspaces/{ws_a.id}/tasks").json()]
        counts = client.get(f"/api/v1/workspaces/{ws_a.id}/inbox/counts").json()
        return listed, tasks, counts["new"]

    do_trash(ws_a, document)
    assert seen() == ([], [], 0)

    do_restore(ws_a, document)

    assert seen() == ([str(document.id)], [str(task.id)], 1)


@pytest.mark.parametrize("call", [trash.trash_document, trash.restore_document])
def test_unknown_letter_is_not_found(ws_a: Workspace, call: Callable[..., Document]) -> None:
    user = admin_of(ws_a)

    with workspace_context(ws_a.id, actor=user.id), pytest.raises(ApiError) as raised:
        call(
            membership=membership_of(ws_a, user),
            user=user,
            actor=actor_of(user),
            document_id=uuid.uuid4(),
        )

    assert (raised.value.status, raised.value.code) == (404, "document_not_found")

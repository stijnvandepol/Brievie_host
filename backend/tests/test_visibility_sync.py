"""`addressee_ids` and the reader rows have one writer (plan Task 8, S 4.9.2, P3)."""

import logging
import uuid
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from apps.documents import organise, services, visibility
from apps.documents.jobs import verify_visibility_job
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentReader,
    DocumentShare,
    DocumentSuggestion,
)
from apps.people.models import Person
from apps.pipeline.autopilot import apply_open_proposals
from apps.rules import jobs as rule_jobs
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, make_document, make_person
from tests.members_support import join
from tests.rules_support import add_addressee, make_rule, remove_rules, text_word
from tests.rules_support import make_document as rule_document

LETTER = "Uitnodiging voor de APK van uw auto met kenteken AB-12-CD."
SYNC = "UPDATE documents_document SET addressee_ids"


def stored(workspace: Workspace, document: Document) -> list[uuid.UUID]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(Document.objects.get(id=document.id).addressee_ids)


def test_addressee_change_outside_a_scope_raises(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    person = make_person(ws_a, "Jan")
    with (
        workspace_context(ws_a.id, actor=SYSTEM),
        pytest.raises(RuntimeError, match="addressee_scope"),
    ):
        organise.add_addressee(document, person, actor=None, origin="user")
    with (
        workspace_context(ws_a.id, actor=SYSTEM),
        pytest.raises(RuntimeError, match="addressee_scope"),
    ):
        organise.set_addressees(document=document, person_ids=[person.id], actor=None)


def test_scope_syncs_once_at_the_outermost_exit(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    jan, piet = make_person(ws_a, "Jan"), make_person(ws_a, "Piet")
    with (
        workspace_context(ws_a.id, actor=SYSTEM),
        CaptureQueriesContext(connection) as queries,
        visibility.addressee_scope(document),
    ):
        organise.add_addressee(document, jan, actor=None, origin="user")
        with visibility.addressee_scope(document):
            organise.add_addressee(document, piet, actor=None, origin="user")
        assert not [q for q in queries if q["sql"].startswith(SYNC)]
    assert len([q for q in queries if q["sql"].startswith(SYNC)]) == 1
    assert stored(ws_a, document) == sorted([jan.id, piet.id])
    assert document.addressee_ids == sorted([jan.id, piet.id])


def test_set_addressees_service_syncs(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    document = make_document(ws_a)
    jan = make_person(ws_a, "Jan")
    with transaction.atomic(), workspace_context(ws_a.id, actor=admin.id):
        services.set_document_addressees(
            workspace=ws_a, user=admin, document_id=document.id, person_ids=[jan.id]
        )
    assert stored(ws_a, document) == [jan.id]


def test_accept_suggestion_syncs(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    document = make_document(ws_a)
    jan = make_person(ws_a, "Jan")
    with workspace_context(ws_a.id, actor=SYSTEM):
        suggestion = DocumentSuggestion.objects.create(
            workspace=ws_a, document=document, action="add_addressee", person=jan, origin="ai"
        )
    with transaction.atomic(), workspace_context(ws_a.id, actor=admin.id):
        services.accept_suggestion(
            workspace=ws_a, user=admin, document_id=document.id, suggestion_id=suggestion.id
        )
    assert stored(ws_a, document) == [jan.id]


def _rule_puts_on(ws: Workspace, person: Person) -> Document:
    remove_rules(ws)
    rule = make_rule(ws, text_word("APK"), actions=[add_addressee(person.id)], mode="auto")
    document = rule_document(ws, LETTER, status="done")
    rule_jobs.apply_rule_to_existing(
        workspace_id=str(ws.id), rule_id=str(rule.id), user_id=str(admin_of(ws).id)
    )
    return document


def test_rule_run_syncs(ws_a: Workspace) -> None:
    jan = make_person(ws_a, "Jan")
    document = _rule_puts_on(ws_a, jan)
    assert stored(ws_a, document) == [jan.id]


def test_undo_syncs(ws_a: Workspace) -> None:
    admin = admin_of(ws_a)
    jan = make_person(ws_a, "Jan")
    document = _rule_puts_on(ws_a, jan)
    with workspace_context(ws_a.id, actor=SYSTEM):
        event = DocumentEvent.objects.get(document=document, kind="addressee_added")
    with transaction.atomic(), workspace_context(ws_a.id, actor=admin.id):
        services.undo_document_event(
            workspace=ws_a, user=admin, document_id=document.id, event_id=event.id
        )
    assert stored(ws_a, document) == []


def test_pipeline_and_autopilot_sync(ws_a: Workspace) -> None:
    document = make_document(ws_a, status="processing")
    jan = make_person(ws_a, "Jan")
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentSuggestion.objects.create(
            workspace=ws_a,
            document=document,
            action="add_addressee",
            person=jan,
            origin="name_match",
            confidence=Decimal("0.950"),
        )
        assert apply_open_proposals(document) == 1
    assert stored(ws_a, document) == [jan.id]


def _add_then_fail(document: Document, person: Person) -> None:
    with visibility.addressee_scope(document):
        organise.add_addressee(document, person, actor=None, origin="user")
        raise ValueError("stop")


def test_rollback_leaves_no_half_sync(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    jan = make_person(ws_a, "Jan")
    with workspace_context(ws_a.id, actor=SYSTEM):
        with pytest.raises(ValueError, match="stop"), transaction.atomic():
            _add_then_fail(document, jan)
        assert not DocumentAddressee.objects.filter(document=document).exists()
        assert not visibility.in_addressee_scope(document.id)
    assert stored(ws_a, document) == []


def test_share_reader_rows_on_a_non_private_letter(ws_a: Workspace) -> None:
    # R44: a share works on every letter, and gives a reader row.
    member = join(ws_a)
    document = make_document(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        DocumentShare.objects.create(workspace=ws_a, document=document, user=member)
        assert visibility.recompute(document) == 1
        rows = list(DocumentReader.objects.filter(document=document).values_list("user", "reason"))
        assert Document.objects.get(id=document.id).visibility == "workspace"
    assert rows == [(member.id, "share")]


def test_nightly_check_repairs_a_hand_damaged_array(
    ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    document = make_document(ws_a)
    stray = uuid.uuid4()
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(addressee_ids=[stray])

    with caplog.at_level(logging.INFO, logger="apps.documents.jobs"):
        verify_visibility_job(0)

    assert stored(ws_a, document) == []
    (line,) = [r for r in caplog.records if r.getMessage().startswith("Visibility check")]
    assert line.levelno == logging.WARNING
    assert line.getMessage() == (
        "Visibility check: 1 addressee lists and 0 reader rows differed;"
        " 0 task assignments released"
    )
    assert str(stray) not in caplog.text


def test_nightly_check_counts_reader_differences(ws_a: Workspace) -> None:
    member = join(ws_a)
    document = make_document(ws_a)
    with workspace_context(ws_a.id, actor=SYSTEM):
        person = Person.objects.create(workspace=ws_a, display_name="Bea", user=member)
        with visibility.addressee_scope(document):
            organise.add_addressee(document, person, actor=None, origin="user")
        Document.objects.filter(id=document.id).update(visibility="restricted")
        assert visibility.recompute(document) == 1
        DocumentReader.objects.filter(document=document).delete()

        check = visibility.verify_visibility(ws_a.id)

        assert check == visibility.VisibilityCheck(addressee_ids_fixed=0, reader_rows_fixed=1)
        assert list(DocumentReader.objects.values_list("user", "reason")) == [
            (member.id, "addressee")
        ]

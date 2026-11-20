import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.documents import organise, suggestions
from apps.documents.models import (
    Document,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.labels.models import Label
from apps.people.models import Person
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, documents_url, make_document, make_person
from tests.helpers import post_json

if TYPE_CHECKING:
    from tests.helpers import Response

ADD_LABEL = DocumentSuggestion.Action.ADD_LABEL
ADD_ADDRESSEE = DocumentSuggestion.Action.ADD_ADDRESSEE


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def label(workspace: Workspace, key: str) -> Label:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Label.objects.get(workspace=workspace, system_key=key)


def propose_label(
    document: Document,
    target: Label,
    *,
    origin: str = "ai",
    confidence: str = "0.600",
    quote: str = "",
    preselected: bool = False,
) -> DocumentSuggestion | None:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return suggestions.propose(
            document,
            action=ADD_LABEL,
            label=target,
            origin=origin,
            quote=quote,
            confidence=Decimal(confidence),
            preselected=preselected,
        )


def propose_person(
    document: Document,
    target: Person,
    *,
    origin: str = "name_match",
    confidence: str = "0.950",
    preselected: bool = True,
) -> DocumentSuggestion | None:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return suggestions.propose(
            document,
            action=ADD_ADDRESSEE,
            person=target,
            origin=origin,
            confidence=Decimal(confidence),
            preselected=preselected,
        )


def rows(document: Document) -> list[DocumentSuggestion]:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return list(DocumentSuggestion.objects.filter(document=document))


def state_of(suggestion: DocumentSuggestion) -> str:
    with workspace_context(suggestion.workspace_id, actor=SYSTEM):
        return DocumentSuggestion.objects.get(id=suggestion.id).state


def label_ids(document: Document) -> set[uuid.UUID]:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return set(
            DocumentLabel.objects.filter(document=document).values_list("label_id", flat=True)
        )


def events(document: Document) -> list[tuple[str, str]]:
    with workspace_context(document.workspace_id, actor=SYSTEM):
        return [
            (e.kind, e.origin)
            for e in DocumentEvent.objects.filter(document=document).order_by("created_at", "id")
        ]


def suggestion_url(document: Document, suggestion_id: object, verb: str) -> str:
    base = f"/workspaces/{document.workspace_id}/documents/{document.id}"
    return f"{base}/suggestions/{suggestion_id}/{verb}"


def accept(client: Client, document: Document, suggestion_id: object) -> "Response":
    return post_json(client, suggestion_url(document, suggestion_id, "accept"))


def dismiss(client: Client, document: Document, suggestion_id: object) -> "Response":
    return post_json(client, suggestion_url(document, suggestion_id, "dismiss"))


def put_labels(client: Client, document: Document, ids: list[uuid.UUID]) -> "Response":
    return client.put(
        f"{documents_url(document.workspace_id)}/{document.id}/labels",
        {"label_ids": [str(i) for i in ids]},
        content_type="application/json",
    )


def test_propose_creates_one_open_row(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg = label(ws_a, "zorg")

    first = propose_label(document, zorg, quote="Zilveren Kruis")
    second = propose_label(document, zorg, quote="zorgverzekeraar")

    assert first is not None
    assert second is not None
    assert first.id == second.id
    [row] = rows(document)
    assert (row.state, row.origin, row.label_id, row.person_id) == ("open", "ai", zorg.id, None)
    assert row.confidence == Decimal("0.600")


def test_two_origins_merge_into_the_strongest(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    anna = make_person(ws_a, "Anna")

    propose_person(document, anna, origin="ai", confidence="0.6", preselected=False)
    with workspace_context(ws_a.id, actor=SYSTEM):
        suggestions.propose(
            document,
            action=ADD_ADDRESSEE,
            person=anna,
            origin="name_match",
            quote="A. de Vries",
            confidence=Decimal("0.95"),
            preselected=True,
        )
    # A weaker origin afterwards changes nothing.
    propose_person(document, anna, origin="ai", confidence="0.7", preselected=False)

    [row] = rows(document)
    assert (row.origin, row.confidence, row.preselected) == ("name_match", Decimal("0.950"), True)
    assert row.quote == "A. de Vries"


def test_propose_masks_bsns_in_the_quote(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")

    row = propose_label(document, label(ws_a, "zorg"), quote="BSN 111222333")

    assert row is not None
    assert "111222333" not in row.quote


def test_propose_skips_what_is_already_there(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg = label(ws_a, "zorg")
    with workspace_context(ws_a.id, actor=SYSTEM):
        organise.add_label(document, zorg, actor=None, origin="user")

    assert propose_label(document, zorg) is None
    assert rows(document) == []


def test_propose_skips_dismissed(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg = label(ws_a, "zorg")
    with workspace_context(ws_a.id, actor=SYSTEM):
        suggestions.mark_dismissed(document, ADD_LABEL, zorg.id)
        assert suggestions.is_dismissed(document, ADD_LABEL, zorg.id)

    assert propose_label(document, zorg, origin="rule", confidence="1") is None
    [row] = rows(document)
    assert row.state == "dismissed"


def test_accepted_row_whose_change_is_gone_opens_again(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg = label(ws_a, "zorg")
    row = propose_label(document, zorg)
    assert row is not None
    with workspace_context(ws_a.id, actor=SYSTEM):
        suggestions.mark_accepted(document, ADD_LABEL, zorg.id)

    assert propose_label(document, zorg) is not None
    assert state_of(row) == "open"


def test_reopened_accepted_row_takes_the_new_proposal(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    anna = make_person(ws_a, "Anna")
    with workspace_context(ws_a.id, actor=SYSTEM):
        suggestions.propose(
            document,
            action=ADD_ADDRESSEE,
            person=anna,
            origin="name_match",
            quote="T.a.v. Anna",
            confidence=Decimal("0.95"),
            preselected=True,
        )
        suggestions.mark_accepted(document, ADD_ADDRESSEE, anna.id)

    propose_person(document, anna, origin="ai", confidence="0.6", preselected=False)

    [row] = rows(document)
    assert (row.state, row.origin, row.quote, row.rule_name) == ("open", "ai", "", "")
    assert (row.confidence, row.preselected) == (Decimal("0.600"), False)


def test_clear_open_removes_only_open_rows_of_those_origins(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg, bank, auto = (label(ws_a, key) for key in ("zorg", "bank", "auto"))
    propose_label(document, zorg, origin="ai")
    propose_label(document, bank, origin="rule", confidence="1")
    propose_label(document, auto, origin="ai")
    with workspace_context(ws_a.id, actor=SYSTEM):
        suggestions.mark_dismissed(document, ADD_LABEL, auto.id)
        suggestions.clear_open(document, ["ai"])

    assert {(r.label_id, r.state) for r in rows(document)} == {
        (bank.id, "open"),
        (auto.id, "dismissed"),
    }


def test_accept_applies_and_writes_event_with_origin_suggestion(
    client_a: Client, ws_a: Workspace
) -> None:
    document = make_document(ws_a, "needs_review")
    zorg = label(ws_a, "zorg")
    anna = make_person(ws_a, "Anna")
    by_label = propose_label(document, zorg)
    by_name = propose_person(document, anna)
    assert by_label is not None
    assert by_name is not None

    r = accept(client_a, document, by_label.id)
    assert r.status_code == 200
    assert r.json()["label_ids"] == [str(zorg.id)]
    assert [s["id"] for s in r.json()["suggestions"]] == [str(by_name.id)]

    r = accept(client_a, document, by_name.id)
    assert r.status_code == 200
    assert r.json()["addressee_ids"] == [str(anna.id)]
    assert r.json()["suggestions"] == []
    assert r.json()["open_suggestion_count"] == 0

    assert events(document) == [("label_added", "suggestion"), ("addressee_added", "suggestion")]
    assert (state_of(by_label), state_of(by_name)) == ("accepted", "accepted")


def test_accept_twice_is_200_and_one_event(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    row = propose_label(document, label(ws_a, "zorg"))
    assert row is not None

    assert accept(client_a, document, row.id).status_code == 200
    assert accept(client_a, document, row.id).status_code == 200

    assert events(document) == [("label_added", "suggestion")]


def test_dismiss_keeps_the_document_unchanged(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg = label(ws_a, "zorg")
    row = propose_label(document, zorg)
    assert row is not None

    r = dismiss(client_a, document, row.id)
    assert r.status_code == 200
    assert dismiss(client_a, document, row.id).status_code == 200

    assert r.json()["suggestions"] == []
    assert label_ids(document) == set()
    assert events(document) == []
    assert state_of(row) == "dismissed"
    # Nothing automatic proposes it again.
    assert propose_label(document, zorg) is None


def test_removing_by_hand_marks_dismissed(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg = label(ws_a, "zorg")
    row = propose_label(document, zorg)
    assert row is not None
    accept(client_a, document, row.id)

    assert put_labels(client_a, document, []).status_code == 200

    assert state_of(row) == "dismissed"
    assert propose_label(document, zorg) is None


def test_removing_an_addressee_by_hand_marks_dismissed(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    anna = make_person(ws_a, "Anna")
    with workspace_context(ws_a.id, actor=SYSTEM):
        organise.set_addressees(document=document, person_ids=[anna.id], actor=None)
        organise.set_addressees(document=document, person_ids=[], actor=None)
        assert suggestions.is_dismissed(document, ADD_ADDRESSEE, anna.id)

    assert propose_person(document, anna) is None


def test_adding_by_hand_after_dismiss_is_allowed(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg = label(ws_a, "zorg")
    row = propose_label(document, zorg)
    assert row is not None
    dismiss(client_a, document, row.id)

    r = put_labels(client_a, document, [zorg.id])

    assert r.status_code == 200
    assert label_ids(document) == {zorg.id}
    assert state_of(row) == "accepted"
    assert events(document) == [("label_added", "user")]


def test_suggestion_of_another_document_is_404(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    other = make_document(ws_a, "needs_review")
    row = propose_label(other, label(ws_a, "zorg"))
    assert row is not None

    for verb in ("accept", "dismiss"):
        r = post_json(client_a, suggestion_url(document, row.id, verb))
        assert r.status_code == 404
        assert r.json()["code"] == "suggestion_not_found"
    assert accept(client_a, document, uuid.uuid4()).status_code == 404
    assert state_of(row) == "open"


def test_suggestion_of_another_workspace_is_404(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    document = make_document(ws_b, "needs_review")
    row = propose_label(document, label(ws_b, "bank"))
    assert row is not None

    r = accept(client_a, document, row.id)

    assert r.status_code == 404
    assert r.json()["code"] == "workspace_not_found"
    assert state_of(row) == "open"


def test_accept_while_processing_is_409(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "processing")
    row = propose_label(document, label(ws_a, "zorg"))
    assert row is not None

    r = accept(client_a, document, row.id)

    assert r.status_code == 409
    assert r.json()["code"] == "document_processing"


def test_document_out_lists_only_open_suggestions(client_a: Client, ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg, bank, auto = (label(ws_a, key) for key in ("zorg", "bank", "auto"))
    anna = make_person(ws_a, "Anna")
    propose_label(document, zorg, confidence="0.700")
    propose_label(document, bank, confidence="0.900", quote="ING")
    dismissed = propose_label(document, auto)
    assert dismissed is not None
    dismiss(client_a, document, dismissed.id)
    by_name = propose_person(document, anna, confidence="0.850")
    assert by_name is not None

    body = client_a.get(f"{documents_url(ws_a.id)}/{document.id}").json()

    # Preselected first, then by confidence.
    assert [(s["label_id"], s["person_id"]) for s in body["suggestions"]] == [
        (None, str(anna.id)),
        (str(bank.id), None),
        (str(zorg.id), None),
    ]
    assert body["suggestions"][1] == {
        "id": body["suggestions"][1]["id"],
        "action": "add_label",
        "label_id": str(bank.id),
        "person_id": None,
        "origin": "ai",
        "rule_name": "",
        "quote": "ING",
        "confidence": "0.900",
        "preselected": False,
    }
    assert body["open_suggestion_count"] == 3
    listed = client_a.get(documents_url(ws_a.id)).json()
    assert [d["open_suggestion_count"] for d in listed] == [3]


def test_deleting_a_label_removes_its_suggestions(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    zorg = label(ws_a, "zorg")
    propose_label(document, zorg)

    with workspace_context(ws_a.id, actor=SYSTEM):
        Label.objects.filter(id=zorg.id).delete()

    assert rows(document) == []


def test_a_suggestion_names_exactly_one_target(ws_a: Workspace) -> None:
    document = make_document(ws_a, "needs_review")
    anna = make_person(ws_a, "Anna")
    with (
        workspace_context(ws_a.id, actor=SYSTEM),
        pytest.raises(IntegrityError),
        transaction.atomic(),
    ):
        DocumentSuggestion.objects.create(
            workspace=ws_a,
            document=document,
            action=ADD_LABEL,
            person=anna,
            origin="ai",
            confidence=Decimal("0.5"),
        )


def test_archiving_a_person_removes_only_their_open_suggestions(
    client_a: Client, ws_a: Workspace
) -> None:
    # A1: an open proposal of an archived person could neither be accepted nor confirmed.
    document = make_document(ws_a, "needs_review")
    other = make_document(ws_a, "needs_review")
    anna, bram = make_person(ws_a, "Anna"), make_person(ws_a, "Bram")
    propose_person(document, anna)
    open_bram = propose_person(document, bram)
    dismissed_anna = propose_person(other, anna)
    assert open_bram is not None
    assert dismissed_anna is not None
    with workspace_context(ws_a.id, actor=SYSTEM):
        suggestions.mark_dismissed(other, ADD_ADDRESSEE, anna.id)

    r = client_a.put(
        f"/api/v1/workspaces/{ws_a.id}/persons/{anna.id}",
        {"display_name": "Anna", "archived": True},
        content_type="application/json",
    )

    assert r.status_code == 200
    assert [s.id for s in rows(document)] == [open_bram.id]
    assert state_of(dismissed_anna) == "dismissed"


def test_an_open_suggestion_of_an_archived_person_is_not_shown(
    client_a: Client, ws_a: Workspace
) -> None:
    document = make_document(ws_a, "needs_review")
    anna, bram = make_person(ws_a, "Anna"), make_person(ws_a, "Bram")
    propose_person(document, anna)
    propose_person(document, bram)
    with workspace_context(ws_a.id, actor=SYSTEM):
        # Behind the service's back: archiving through the service removes the row.
        Person.objects.filter(id=anna.id).update(archived_at=timezone.now())

    body = client_a.get(f"{documents_url(ws_a.id)}/{document.id}").json()

    assert [s["person_id"] for s in body["suggestions"]] == [str(bram.id)]
    assert body["open_suggestion_count"] == 1
    listed = client_a.get(documents_url(ws_a.id)).json()
    assert [d["open_suggestion_count"] for d in listed] == [1]


def test_a_document_reads_its_suggestions_once(client_a: Client, ws_a: Workspace) -> None:
    # A2
    document = make_document(ws_a, "needs_review")
    propose_person(document, make_person(ws_a, "Anna"))
    propose_label(document, label(ws_a, "zorg"))

    with CaptureQueriesContext(connection) as queries:
        body = client_a.get(f"{documents_url(ws_a.id)}/{document.id}").json()

    assert body["open_suggestion_count"] == 2
    reads = [q for q in queries if 'FROM "documents_documentsuggestion"' in q["sql"]]
    assert len(reads) == 1

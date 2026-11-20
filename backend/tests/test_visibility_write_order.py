"""The write order (plan Task 9, S 4.9.3): confirming or organising a letter one hands
over to someone else stores everything and answers `can_read: false`, never 42501.

The office is in state E off: Piet sees `scanned_in_review` only because he scanned it,
and `own_piet` only because it is on his name.
"""

from typing import TYPE_CHECKING

import pytest
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.documents import review
from apps.documents.models import (
    Document,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.documents.schemas import ConfirmIn
from apps.labels.models import Label
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM
from tests.helpers import post_json
from tests.rules_support import add_label, make_rule, remove_rules, sender_contains
from tests.visibility_support import Office, as_actor, office

if TYPE_CHECKING:
    from tests.helpers import Response

TASK = {
    "type": "pay",
    "description": "Betaal de rekening",
    "deadline": "2026-12-01",
    "deadline_source": "manual",
}


@pytest.fixture
def o(db: None) -> Office:
    return office()


def _as(o: Office, user_name: str) -> Client:
    client = Client()
    client.force_login(o.users[user_name])
    return client


def _body(o: Office, *, addressees: list[str], sender: str = "Gemeente") -> dict[str, object]:
    return {
        "sender": sender,
        "document_type": "",
        "summary": "",
        "summary_by_ai": False,
        "tasks": [TASK],
        "sets_organisation": True,
        "label_ids": [],
        "addressee_ids": [str(o.persons[name].id) for name in addressees],
    }


def _confirm(client: Client, o: Office, letter: str, body: dict[str, object]) -> "Response":
    return post_json(
        client, f"/workspaces/{o.workspace.id}/documents/{o.letters[letter].id}/confirm", body
    )


def test_confirm_handing_over_answers_can_read_false(o: Office) -> None:
    letter = o.letters["scanned_in_review"]
    with as_actor(o.workspace, SYSTEM):
        open_before = DocumentSuggestion.objects.filter(document=letter, state="open").count()
    assert open_before > 0

    response = _confirm(_as(o, "plain"), o, "scanned_in_review", _body(o, addressees=["Femke"]))

    assert response.status_code == 200, response.json()
    answer = response.json()
    assert answer["can_read"] is False
    assert answer["id"] == str(letter.id)
    assert answer["status"] == "done"
    assert (answer["pages"], answer["label_ids"], answer["addressee_ids"], answer["tasks"]) == (
        [],
        [],
        [],
        [],
    )
    assert answer["sender"] == ""
    assert o.visible(o.plain.id) == {"own_piet"}
    with as_actor(o.workspace, SYSTEM):
        stored = Document.objects.get(id=letter.id)
        assert (stored.status, stored.confirmed_by_id, stored.sender) == (
            "done",
            o.plain.id,
            "Gemeente",
        )
        made = Task.objects.get(document=letter, description="Betaal de rekening")
        assert made.created_by_id == o.plain.id
        assert not DocumentSuggestion.objects.filter(document=letter, state="open").exists()
        assert DocumentEvent.objects.filter(document=letter, kind="confirmed").count() == 1
        assert stored.addressee_ids == [o.persons["Femke"].id]


def test_confirm_never_raises_42501(o: Office) -> None:
    remove_rules(o.workspace)
    with as_actor(o.workspace, SYSTEM):
        label = Label.objects.create(workspace=o.workspace, name="Gemeente")
    make_rule(o.workspace, sender_contains("Gemeente"), actions=[add_label(label)], mode="auto")

    response = _confirm(_as(o, "plain"), o, "scanned_in_review", _body(o, addressees=["Femke"]))

    assert response.status_code == 200, response.json()
    assert response.json()["can_read"] is False
    with as_actor(o.workspace, SYSTEM):
        assert DocumentLabel.objects.filter(
            document=o.letters["scanned_in_review"], label=label
        ).exists()


def test_repeat_confirm_after_hand_over(o: Office) -> None:
    client = _as(o, "plain")
    body = _body(o, addressees=["Femke"])
    first = _confirm(client, o, "scanned_in_review", body)

    again = _confirm(client, o, "scanned_in_review", body)

    assert again.status_code == 200, again.json()
    assert again.json() == first.json()
    with as_actor(o.workspace, SYSTEM):
        events = DocumentEvent.objects.filter(
            document=o.letters["scanned_in_review"], kind="confirmed"
        )
        assert events.count() == 1


def test_other_review_after_hand_over_is_404(o: Office) -> None:
    client = _as(o, "plain")
    _confirm(client, o, "scanned_in_review", _body(o, addressees=["Femke"]))

    other = _confirm(client, o, "scanned_in_review", _body(o, addressees=["Femke"], sender="X"))

    assert other.status_code == 404
    assert other.json()["code"] == "document_not_found"


def test_set_addressees_handing_over_answers_can_read_false(o: Office) -> None:
    letter = o.letters["own_piet"]
    response = _as(o, "plain").put(
        f"/api/v1/workspaces/{o.workspace.id}/documents/{letter.id}/addressees",
        {"person_ids": [str(o.persons["Jan"].id)]},
        content_type="application/json",
    )

    assert response.status_code == 200, response.json()
    assert response.json()["can_read"] is False
    assert response.json()["addressee_ids"] == []
    assert "own_piet" not in o.visible(o.plain.id)
    with as_actor(o.workspace, SYSTEM):
        assert Document.objects.get(id=letter.id).addressee_ids == [o.persons["Jan"].id]


def test_confirm_keeping_the_letter_answers_the_full_document(o: Office) -> None:
    response = _confirm(_as(o, "finance"), o, "scanned_in_review", _body(o, addressees=["Femke"]))

    assert response.status_code == 200, response.json()
    answer = response.json()
    assert answer["can_read"] is True
    assert answer["addressee_ids"] == [str(o.persons["Femke"].id)]
    assert answer["sender"] == "Gemeente"
    assert len(answer["tasks"]) == 2


def test_status_written_last_as_system(o: Office) -> None:
    data = ConfirmIn.model_validate(_body(o, addressees=["Femke"]))
    with (
        transaction.atomic(),
        as_actor(o.workspace, o.plain.id),
        CaptureQueriesContext(connection) as queries,
    ):
        review.confirm_document(
            workspace=o.workspace,
            user=o.plain,
            document_id=o.letters["scanned_in_review"].id,
            data=data,
        )
    sql = [q["sql"] for q in queries]
    sync = next(
        i for i, q in enumerate(sql) if q.startswith("UPDATE documents_document SET addressee_ids")
    )
    status = next(
        i for i, q in enumerate(sql) if q.startswith('UPDATE "documents_document" SET "status"')
    )
    assert sync < status
    assert not any(
        '"status"' in q for q in sql[:sync] if q.startswith('UPDATE "documents_document"')
    )


def test_reopen_of_a_hidden_letter_is_404(o: Office) -> None:
    letter = o.letters["finance_letter"]
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=letter.id).update(confirmed_by_ai=True)

    response = post_json(
        _as(o, "plain"), f"/workspaces/{o.workspace.id}/documents/{letter.id}/reopen"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "document_not_found"

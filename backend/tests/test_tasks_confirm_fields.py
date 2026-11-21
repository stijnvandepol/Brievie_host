"""Confirm stores a task's amount and the term it came from; the autopilot fills a
payment's amount only from a quoted invoice amount (spec S 12, plan Task 3)."""

import copy
from datetime import date
from decimal import Decimal

import pytest
from django.test import Client

from apps.documents.models import Document
from apps.pipeline.autopilot import decide_confirmation
from apps.pipeline.schema import ExtractionPayload
from apps.pipeline.validation import validate
from apps.tasks.models import Task
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, jpeg, upload
from tests.helpers import post_json
from tests.test_documents_review_api import confirm, review, task

pytestmark = pytest.mark.usefixtures("without_preset_rules")

TERM_TEXT = (
    "Gemeente Utrecht\nDatum 3 november 2026\n"
    "U kunt binnen zes weken na dagtekening bezwaar maken. Dat kan ook online."
)


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def _letter(client: Client, workspace: Workspace, text: str) -> str:
    document_id = str(upload(client, workspace.id, [jpeg()], texts=[text]).json()["id"])
    with workspace_context(workspace.id, actor=SYSTEM):
        Document.objects.filter(id=document_id).update(status="needs_review")
    return document_id


def _tasks(workspace: Workspace) -> list[Task]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(Task.objects.order_by("created_at", "id"))


def _undated(**changes: object) -> dict[str, object]:
    body = task(
        type="object",
        description="Maak bezwaar",
        deadline=None,
        deadline_source="manual",
        deadline_quote="",
    )
    body.update(changes)
    return body


TEXT = "Belastingdienst\nDatum 3 november 2026\nU moet € 1.234,56 betalen vóór 1 december 2026."


def test_confirm_stores_amount(client_a: Client, ws_a: Workspace) -> None:
    document_id = _letter(client_a, ws_a, TEXT)
    r = confirm(client_a, ws_a.id, document_id, review(tasks=[task(amount="1234,56")]))
    assert r.status_code == 200, r.json()
    assert _tasks(ws_a)[0].amount == Decimal("1234.56")
    assert r.json()["tasks"][0]["amount"] == "1234.56"


def test_confirm_without_amount_stores_none(client_a: Client, ws_a: Workspace) -> None:
    document_id = _letter(client_a, ws_a, TEXT)
    r = confirm(client_a, ws_a.id, document_id, review())  # an older client's body
    assert r.status_code == 200
    assert _tasks(ws_a)[0].amount is None
    assert r.json()["tasks"][0]["amount"] == ""


@pytest.mark.parametrize("amount", ["12,345", "-1", "abc", "1.234,56", "12345678901"])
def test_confirm_rejects_a_bad_amount(client_a: Client, ws_a: Workspace, amount: str) -> None:
    document_id = _letter(client_a, ws_a, TEXT)
    r = confirm(client_a, ws_a.id, document_id, review(tasks=[task(amount=amount)]))
    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert _tasks(ws_a) == []


def test_undated_task_gets_the_term_phrase(client_a: Client, ws_a: Workspace) -> None:
    document_id = _letter(client_a, ws_a, TERM_TEXT)
    r = confirm(client_a, ws_a.id, document_id, review(tasks=[_undated()]))
    assert r.status_code == 200, r.json()
    stored = _tasks(ws_a)[0]
    assert stored.term_quote == "binnen zes weken na dagtekening bezwaar maken"
    assert r.json()["tasks"][0]["term_quote"] == stored.term_quote


def test_dated_task_gets_no_term_phrase(client_a: Client, ws_a: Workspace) -> None:
    document_id = _letter(client_a, ws_a, TERM_TEXT)
    body = review(tasks=[_undated(deadline="2026-12-15")])
    r = confirm(client_a, ws_a.id, document_id, body)
    assert r.status_code == 200, r.json()
    assert _tasks(ws_a)[0].term_quote == ""


def test_repeat_confirm_with_amount_matches(client_a: Client, ws_a: Workspace) -> None:
    document_id = _letter(client_a, ws_a, TEXT)
    body = review(tasks=[task(amount="1234.56")])
    first = confirm(client_a, ws_a.id, document_id, body)
    again = confirm(client_a, ws_a.id, document_id, body)
    assert first.status_code == again.status_code == 200
    assert len(_tasks(ws_a)) == 1
    other = confirm(client_a, ws_a.id, document_id, review(tasks=[task(amount="99")]))
    assert other.status_code == 409


def test_reconfirm_after_dating_makes_no_second_task(client_a: Client, ws_a: Workspace) -> None:
    document_id = _letter(client_a, ws_a, TERM_TEXT)
    body = review(tasks=[_undated()])
    assert confirm(client_a, ws_a.id, document_id, body).status_code == 200
    (stored,) = _tasks(ws_a)
    dated = post_json(
        client_a,
        f"/workspaces/{ws_a.id}/tasks/{stored.id}/deadline",
        {"deadline": "2026-12-15"},
    )
    assert dated.status_code == 200, dated.json()
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document_id).update(status="needs_review")

    assert confirm(client_a, ws_a.id, document_id, body).status_code == 200
    (only,) = _tasks(ws_a)
    assert only.deadline == date(2026, 12, 15)


AUTOPILOT_TEXT = (
    "Energie Noord BV\nDatum 3 november 2026\nTotaal te betalen € 1.234,56 vóór 1 december 2026."
)
AUTOPILOT: dict[str, object] = {
    "afzender": {"naam": "Energie Noord BV", "bron": "Energie Noord BV", "zekerheid": 0.95},
    "documenttype": "factuur",
    "dagtekening": {"waarde": "2026-11-03", "bron": "Datum 3 november 2026", "zekerheid": 0.95},
    "samenvatting_b1": "Je moet de rekening betalen.",
    "acties": [
        {
            "type": "betalen",
            "omschrijving": "Betaal de rekening",
            "deadline": {
                "waarde": "2026-12-01",
                "bron": "vóór 1 december 2026",
                "zekerheid": 0.95,
            },
        }
    ],
    "factuur": {
        "nummer": None,
        "vervaldatum": None,
        "bedrag_incl": "1234.56",
        "bron": "€ 1.234,56",
        "zekerheid": 0.95,
    },
}


def _decide(raw: dict[str, object]) -> str:
    today = date(2026, 11, 14)
    payload = ExtractionPayload.model_validate(raw)
    values = validate(payload, AUTOPILOT_TEXT, today=today)
    data = decide_confirmation(payload=payload, values=values, text=AUTOPILOT_TEXT, today=today)
    assert data is not None
    (only,) = data.tasks
    return only.amount


def test_autopilot_pay_task_takes_a_quoted_amount() -> None:
    assert _decide(AUTOPILOT) == "1234.56"


def test_autopilot_pay_task_skips_an_unquoted_amount() -> None:
    raw = copy.deepcopy(AUTOPILOT)
    invoice = raw["factuur"]
    assert isinstance(invoice, dict)
    invoice["bedrag_incl"] = "999.99"  # not in the letter: confidence 0
    invoice["bron"] = "€ 999,99"
    assert _decide(raw) == ""


def test_autopilot_other_task_has_no_amount() -> None:
    raw = copy.deepcopy(AUTOPILOT)
    actions = raw["acties"]
    assert isinstance(actions, list)
    first = actions[0]
    assert isinstance(first, dict)
    first["type"] = "reageren"
    assert _decide(raw) == ""

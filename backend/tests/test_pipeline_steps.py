import json
import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from django.db import connection

from apps.documents.models import Document, Extraction
from apps.pipeline.errors import PermanentError
from apps.pipeline.providers import LLMProvider, ProviderUnavailable
from apps.pipeline.steps import run_pipeline
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace

TEXT = "Belastingdienst\nDatum 3 november 2026\nU moet € 1.234,56 betalen vóór 1 december 2026."
TODAY = date(2026, 11, 14)


def answer(**changes: object) -> str:
    data: dict[str, object] = {
        "afzender": {"naam": "Belastingdienst", "bron": "Belastingdienst", "zekerheid": 0.97},
        "documenttype": "aanslag",
        "dagtekening": {"waarde": "2026-11-03", "bron": "Datum 3 november 2026", "zekerheid": 0.9},
        "samenvatting_b1": "Je moet belasting betalen.",
        "acties": [
            {
                "type": "betalen",
                "omschrijving": "Betaal de aanslag",
                "deadline": {
                    "waarde": "2026-12-01",
                    "bron": "vóór 1 december 2026",
                    "zekerheid": 0.95,
                },
            }
        ],
        "factuur": None,
    }
    data.update(changes)
    return json.dumps(data)


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.texts: list[str] = []
        self.in_transaction: list[bool] = []
        self.during_call: Callable[[], None] = lambda: None

    def extract(self, text: str) -> str:
        self.texts.append(text)
        self.in_transaction.append(connection.in_atomic_block)
        self.during_call()
        result = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(result, Exception):
            raise result
        return result


def make_document(workspace: Workspace, text: str = TEXT, **fields: object) -> Document:
    with workspace_context(workspace.id):
        return Document.objects.create(
            workspace=workspace, client_id=uuid.uuid4(), page_count=1, ocr_text=text, **fields
        )


def run(workspace: Workspace, document: Document, provider: LLMProvider | None) -> None:
    run_pipeline(
        workspace.id, document.id, provider_factory=lambda _: provider, today=lambda: TODAY
    )


def state(workspace: Workspace, document: Document) -> tuple[str, str, list[Extraction]]:
    with workspace_context(workspace.id):
        fresh = Document.objects.get(id=document.id)
        return fresh.status, fresh.failure_code, list(fresh.extractions.all())


def test_proposals_are_stored_with_quote_confidence_and_provider(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    provider = FakeProvider(answer())

    run(ws_a, document, provider)

    status, _, proposals = state(ws_a, document)
    assert status == "needs_review"
    assert provider.texts == [TEXT]
    by_field = {(p.field, p.group): p for p in proposals}
    deadline = by_field["action_deadline", 1]
    assert deadline.value == "2026-12-01"
    assert deadline.quote == "vóór 1 december 2026"
    assert deadline.confidence == Decimal("0.950")
    assert (deadline.provider, deadline.model) == ("fake", "fake-1")
    assert by_field["sender", 0].value == "Belastingdienst"
    assert by_field["action_type", 1].value == "pay"
    assert by_field["summary", 0].value == "Je moet belasting betalen."
    assert all(p.workspace_id == ws_a.id for p in proposals)


def test_hallucinated_deadline_gets_confidence_zero(ws_a: Workspace) -> None:
    # Review Focus 3
    document = make_document(ws_a)
    invented = [
        {
            "type": "betalen",
            "omschrijving": "Betaal",
            "deadline": {
                "waarde": "2026-12-15",
                "bron": "vóór 15 december 2026",
                "zekerheid": 0.99,
            },
        }
    ]

    run(ws_a, document, FakeProvider(answer(acties=invented)))

    _, _, proposals = state(ws_a, document)
    (deadline,) = [p for p in proposals if p.field == "action_deadline"]
    assert deadline.confidence == Decimal("0")
    assert deadline.needs_attention


def test_running_twice_leaves_one_set_of_proposals(ws_a: Workspace) -> None:
    # Review Focus 2: a job is retried after the worker died.
    document = make_document(ws_a)
    run(ws_a, document, FakeProvider(answer()))
    _, _, first = state(ws_a, document)
    with workspace_context(ws_a.id):
        Document.objects.filter(id=document.id).update(status="processing")

    run(ws_a, document, FakeProvider(answer()))

    _, _, second = state(ws_a, document)
    assert len(second) == len(first)
    assert {p.id for p in second}.isdisjoint({p.id for p in first})


def test_malformed_json_once_is_tried_again(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    provider = FakeProvider("dit is geen json", answer())

    run(ws_a, document, provider)

    assert state(ws_a, document)[0] == "needs_review"
    assert len(provider.texts) == 2


@pytest.mark.parametrize("bad", ["geen json", '{"afzender": 1}', "[]", ""])
def test_malformed_json_twice_is_invalid_output(ws_a: Workspace, bad: str) -> None:
    document = make_document(ws_a)
    provider = FakeProvider(bad)

    with pytest.raises(PermanentError) as excinfo:
        run(ws_a, document, provider)

    assert excinfo.value.code == "ai_invalid_output"
    assert len(provider.texts) == 2
    assert state(ws_a, document) == ("processing", "", [])


def test_json_in_a_code_fence_is_accepted(ws_a: Workspace) -> None:
    document = make_document(ws_a)

    run(ws_a, document, FakeProvider(f"```json\n{answer()}\n```"))

    assert state(ws_a, document)[0] == "needs_review"


def test_schema_error_text_is_not_logged(ws_a: Workspace, caplog: pytest.LogCaptureFixture) -> None:
    document = make_document(ws_a)
    leaking = json.dumps({"afzender": "een regel uit de brief van Jan"})

    with caplog.at_level("DEBUG"), pytest.raises(PermanentError) as excinfo:
        run(ws_a, document, FakeProvider(leaking))

    assert "een regel uit de brief" not in caplog.text
    assert "een regel uit de brief" not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


def test_unavailable_provider_propagates_and_the_document_stays_processing(
    ws_a: Workspace,
) -> None:
    document = make_document(ws_a)

    with pytest.raises(ProviderUnavailable):
        run(ws_a, document, FakeProvider(ProviderUnavailable()))

    assert state(ws_a, document) == ("processing", "", [])


@pytest.mark.django_db(transaction=True)
def test_no_transaction_is_open_during_the_ai_call(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    provider = FakeProvider(answer())

    run(ws_a, document, provider)

    assert provider.in_transaction == [False]
    assert state(ws_a, document)[0] == "needs_review"


def test_document_confirmed_during_the_ai_call_is_left_alone(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    provider = FakeProvider(answer())

    def confirm_meanwhile() -> None:
        with workspace_context(ws_a.id):
            Document.objects.filter(id=document.id).update(status="done")

    provider.during_call = confirm_meanwhile

    run(ws_a, document, provider)

    assert state(ws_a, document) == ("done", "", [])


def test_ai_off_gives_needs_review_without_proposals(ws_a: Workspace) -> None:
    document = make_document(ws_a)

    run(ws_a, document, None)

    assert state(ws_a, document) == ("needs_review", "", [])


def test_provider_gets_masked_text_even_when_storage_was_not(ws_a: Workspace) -> None:
    # Review Focus 4: a row written without the intake's masking.
    document = make_document(ws_a, text="Uw BSN 111222333. " + TEXT)
    provider = FakeProvider(answer())

    run(ws_a, document, provider)

    assert "111222333" not in provider.texts[0]
    assert "[BSN]" in provider.texts[0]


def test_default_provider_comes_from_the_workspace_settings(ws_a: Workspace) -> None:
    # No settings row: AI is off.
    document = make_document(ws_a)

    run_pipeline(ws_a.id, document.id)

    assert state(ws_a, document) == ("needs_review", "", [])


def test_document_of_another_workspace_is_not_processed(ws_a: Workspace, ws_b: Workspace) -> None:
    document = make_document(ws_a)
    provider = FakeProvider(answer())

    run_pipeline(ws_b.id, UUID(str(document.id)), provider_factory=lambda _: provider)

    assert provider.texts == []
    assert state(ws_a, document) == ("processing", "", [])


def test_provider_with_a_long_host_name_stores_its_proposals(ws_a: Workspace) -> None:
    document = make_document(ws_a)
    provider = FakeProvider(answer())
    provider.name = "ollama-inference-gateway.huis-van-de-familie.example-lan.local"

    run(ws_a, document, provider)

    status, code, proposals = state(ws_a, document)
    assert (status, code) == ("needs_review", "")
    assert {p.provider for p in proposals} == {provider.name}

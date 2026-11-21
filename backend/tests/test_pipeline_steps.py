import json
import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from django.db import connection
from django.utils import timezone

from apps.documents import organise, suggestions
from apps.documents.models import Document, DocumentSuggestion, Extraction
from apps.labels.models import Label
from apps.people.models import Person
from apps.pipeline import jobs, steps
from apps.pipeline.choices import ExtractionRequest
from apps.pipeline.errors import PermanentError
from apps.pipeline.providers import LLMProvider, ProviderUnavailable
from apps.pipeline.steps import run_pipeline
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.rules_support import add_label, make_rule, text_word

# The preset rules would add their own proposals to these letters.
pytestmark = pytest.mark.usefixtures("without_preset_rules")

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


Answer = str | Exception | Callable[[ExtractionRequest], str]


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, *answers: Answer) -> None:
        self.answers = list(answers)
        self.texts: list[str] = []
        self.requests: list[ExtractionRequest] = []
        self.in_transaction: list[bool] = []
        self.during_call: Callable[[], None] = lambda: None

    def extract(self, request: ExtractionRequest) -> str:
        text = request.text
        self.texts.append(text)
        self.requests.append(request)
        self.in_transaction.append(connection.in_atomic_block)
        self.during_call()
        result = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(request)
        return result


def make_document(workspace: Workspace, text: str = TEXT, **fields: object) -> Document:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Document.objects.create(
            workspace=workspace, client_id=uuid.uuid4(), page_count=1, ocr_text=text, **fields
        )


def run(workspace: Workspace, document: Document, provider: LLMProvider | None) -> None:
    run_pipeline(
        workspace.id, document.id, provider_factory=lambda _: provider, today=lambda: TODAY
    )


def state(workspace: Workspace, document: Document) -> tuple[str, str, list[Extraction]]:
    with workspace_context(workspace.id, actor=SYSTEM):
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
    with workspace_context(ws_a.id, actor=SYSTEM):
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
        with workspace_context(ws_a.id, actor=SYSTEM):
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


# Suggestions at the end of processing (design 10 and 17).

LETTER = "T.a.v. J. de Vries\nBelastingdienst\nU moet betalen vóór 1 december 2026."


def make_person(workspace: Workspace, name: str, *variants: str, archived: bool = False) -> Person:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Person.objects.create(
            workspace=workspace,
            display_name=name,
            name_variants=list(variants),
            archived_at=timezone.now() if archived else None,
        )


def preset_label(workspace: Workspace, key: str) -> Label:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Label.objects.get(system_key=key)


def open_suggestions(workspace: Workspace, document: Document) -> list[DocumentSuggestion]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(
            DocumentSuggestion.objects.filter(document=document, state="open").order_by("origin")
        )


def picking(
    *, persons: dict[str, str] | None = None, labels: list[str] | None = None
) -> Callable[[ExtractionRequest], str]:
    """An answer that picks persons (name -> quote) and labels by name from the request."""

    def build(request: ExtractionRequest) -> str:
        person_alias = {choice.name: choice.alias for choice in request.persons}
        label_alias = {choice.name: choice.alias for choice in request.labels}
        return answer(
            op_naam_van=[
                {"id": person_alias[name], "bron": quote, "zekerheid": 0.99}
                for name, quote in (persons or {}).items()
            ],
            labels=[{"id": label_alias[name], "zekerheid": 0.95} for name in labels or []],
        )

    return build


def test_name_in_letter_becomes_a_preselected_suggestion(ws_a: Workspace) -> None:
    jan = make_person(ws_a, "Jan de Vries", "J. de Vries")
    # An archived person whose variant is in the letter is not proposed.
    make_person(ws_a, "Oud", "J. de Vries", archived=True)
    document = make_document(ws_a, text=LETTER)

    run(ws_a, document, None)

    [row] = open_suggestions(ws_a, document)
    assert (row.action, row.person_id, row.origin) == ("add_addressee", jan.id, "name_match")
    assert row.preselected
    assert row.confidence == Decimal("0.950")
    assert "J. de Vries" in row.quote
    assert state(ws_a, document)[0] == "needs_review"


def test_ai_picks_become_suggestions_never_preselected(ws_a: Workspace) -> None:
    emma = make_person(ws_a, "Emma")
    belasting = preset_label(ws_a, "belastingdienst")
    document = make_document(ws_a, text=LETTER)
    provider = FakeProvider(
        picking(persons={"Emma": "T.a.v. J. de Vries"}, labels=["Belastingdienst"])
    )

    run(ws_a, document, provider)

    rows = open_suggestions(ws_a, document)
    assert {(r.action, r.person_id or r.label_id, r.origin) for r in rows} == {
        ("add_addressee", emma.id, "ai"),
        ("add_label", belasting.id, "ai"),
    }
    assert not any(r.preselected for r in rows)
    by_label = next(r for r in rows if r.label_id is not None)
    assert by_label.confidence == Decimal("0.800")


def test_ai_pick_of_deleted_label_is_skipped(ws_a: Workspace) -> None:
    # Review Focus 1
    belasting = preset_label(ws_a, "belastingdienst")
    zorg = preset_label(ws_a, "zorg")
    emma = make_person(ws_a, "Emma")
    document = make_document(ws_a, text=LETTER)
    provider = FakeProvider(
        picking(persons={"Emma": "Belastingdienst"}, labels=["Belastingdienst", "Zorg"])
    )

    def delete_meanwhile() -> None:
        with workspace_context(ws_a.id, actor=SYSTEM):
            Label.objects.filter(id=belasting.id).delete()
            Person.objects.filter(id=emma.id).update(archived_at=timezone.now())

    provider.during_call = delete_meanwhile

    run(ws_a, document, provider)

    assert [(r.label_id, r.person_id) for r in open_suggestions(ws_a, document)] == [
        (zorg.id, None)
    ]


def test_rerun_replaces_open_and_keeps_dismissed(ws_a: Workspace) -> None:
    jan = make_person(ws_a, "Jan de Vries", "J. de Vries")
    belasting, zorg, auto, bank = (
        preset_label(ws_a, key) for key in ("belastingdienst", "zorg", "auto", "bank")
    )
    document = make_document(ws_a, text=LETTER)
    run(ws_a, document, FakeProvider(picking(labels=["Belastingdienst", "Zorg", "Bank"])))
    with workspace_context(ws_a.id, actor=SYSTEM):
        suggestions.mark_dismissed(document, "add_label", belasting.id)
        organise.add_label(document, zorg, actor=None, origin="suggestion")
        suggestions.mark_accepted(document, "add_label", zorg.id)
        accepted = DocumentSuggestion.objects.get(document=document, label_id=zorg.id)
        Document.objects.filter(id=document.id).update(status="processing")

    run(ws_a, document, FakeProvider(picking(labels=["Belastingdienst", "Auto", "Zorg"])))

    assert {(r.label_id, r.person_id) for r in open_suggestions(ws_a, document)} == {
        (None, jan.id),
        (auto.id, None),
    }
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert suggestions.is_dismissed(document, "add_label", belasting.id)
        assert not DocumentSuggestion.objects.filter(label_id=bank.id).exists()
        after = DocumentSuggestion.objects.get(id=accepted.id)
    assert (after.state, after.origin, after.confidence, after.updated_at) == (
        "accepted",
        accepted.origin,
        accepted.confidence,
        accepted.updated_at,
    )


def test_failed_processing_still_proposes_the_name(ws_a: Workspace) -> None:
    jan = make_person(ws_a, "Jan de Vries", "J. de Vries")
    document = make_document(ws_a, text=LETTER)
    provider = FakeProvider(PermanentError("ai_rejected"))

    jobs.run_job(
        ws_a.id,
        document.id,
        attempts=0,
        run=lambda w, d: run_pipeline(w, d, provider_factory=lambda _: provider),
    )

    assert state(ws_a, document)[:2] == ("failed", "ai_rejected")
    [row] = open_suggestions(ws_a, document)
    assert (row.person_id, row.origin, row.preselected) == (jan.id, "name_match", True)


def _failing_match(*args: object) -> list[object]:
    raise RuntimeError("matching broke")


def test_failure_while_proposing_still_marks_the_document_failed(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # mark_failed is run_job's last resort: it must always end in `failed`.
    monkeypatch.setattr(steps, "match_addressees", _failing_match)
    document = make_document(ws_a, text=LETTER)
    provider = FakeProvider(PermanentError("ai_rejected"))

    jobs.run_job(
        ws_a.id,
        document.id,
        attempts=0,
        run=lambda w, d: run_pipeline(w, d, provider_factory=lambda _: provider),
    )

    assert state(ws_a, document)[:2] == ("failed", "ai_rejected")


def test_failure_while_organising_keeps_the_ais_proposals(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # B3: organising runs in a savepoint; the AI's result is not lost with it.
    monkeypatch.setattr(steps, "match_addressees", _failing_match)
    document = make_document(ws_a, text=LETTER)
    provider = FakeProvider(answer())

    jobs.run_job(
        ws_a.id,
        document.id,
        attempts=0,
        run=lambda w, d: run_pipeline(w, d, provider_factory=lambda _: provider),
    )

    status, failure_code, proposals = state(ws_a, document)
    assert (status, failure_code) == ("needs_review", "")
    assert proposals
    assert open_suggestions(ws_a, document) == []
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert errors == [f"Unexpected RuntimeError while proposing for document {document.id}"]


def test_rule_takes_over_the_ais_proposal_of_the_same_label(ws_a: Workspace) -> None:
    belasting = preset_label(ws_a, "belastingdienst")
    make_rule(ws_a, text_word("Belastingdienst"), actions=[add_label(belasting)], name="Fiscus")
    document = make_document(ws_a, text=LETTER)

    run(ws_a, document, FakeProvider(picking(labels=["Belastingdienst"])))

    [row] = open_suggestions(ws_a, document)
    assert (row.label_id, row.origin, row.rule_name) == (belasting.id, "rule", "Fiscus")
    assert row.quote == "Belastingdienst"
    assert row.confidence == Decimal("1.000")


def test_a_masked_variant_from_before_is_neither_proposed_nor_sent_to_the_ai(
    ws_a: Workspace,
) -> None:
    # The finding: variant "123456782" was stored as "[BSN]"; a letter with another BSN
    # then proposed and preselected this person.
    bsn_test = make_person(ws_a, "BSN-test", "[BSN]")
    document = make_document(ws_a, text="Burgerservicenummer: [BSN]\nT.a.v. de heer K. Bakker")

    with workspace_context(ws_a.id, actor=SYSTEM):
        names = {p.person_id: p.names for p in steps.active_persons()}
        assert names[bsn_test.id] == ("BSN-test",)
    run(ws_a, document, None)

    assert open_suggestions(ws_a, document) == []

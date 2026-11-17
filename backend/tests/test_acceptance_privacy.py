"""Acceptance criteria from spec section 16 about what leaves the server and BSNs."""

import json

import httpx
import pytest
from django.test import Client

from apps.documents.models import Document, DocumentPage, DocumentSuggestion, Extraction
from apps.labels.models import Label
from apps.people.models import Person
from apps.pipeline import jobs
from apps.pipeline.choices import ExtractionRequest, choices_block
from apps.pipeline.providers import OpenAICompatibleProvider
from apps.pipeline.schema import json_schema
from apps.pipeline.steps import run_pipeline
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, jpeg, upload

BSN = "111222333"
LETTER = f"CAK\nUw BSN {BSN}\nU moet betalen vóór 1 december 2026."


class QuotingProvider:
    """Answers with quotes taken from the text it was given."""

    name = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.texts: list[str] = []

    def extract(self, request: ExtractionRequest) -> str:
        text = request.text
        self.texts.append(text)
        line = next(line for line in text.splitlines() if "BSN" in line)
        return json.dumps(
            {
                "afzender": {"naam": "CAK", "bron": "CAK", "zekerheid": 0.9},
                "documenttype": "rekening",
                "dagtekening": {"waarde": None, "bron": None, "zekerheid": 0},
                "samenvatting_b1": f"Je moet betalen. {line}",
                "acties": [
                    {
                        "type": "betalen",
                        "omschrijving": line,
                        "deadline": {
                            "waarde": "2026-12-01",
                            "bron": f"{line}\nU moet betalen vóór 1 december 2026.",
                            "zekerheid": 0.9,
                        },
                    }
                ],
                "factuur": None,
            }
        )


def test_ai_off_never_calls_a_provider(
    client: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # "Geen documentinhoud verlaat de server bij AI local of off."
    def no_network(*args: object, **kwargs: object) -> httpx.Response:
        pytest.fail("A request left the server although AI is off.")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(OpenAICompatibleProvider, "extract", no_network)
    client.force_login(admin_of(ws_a))
    document_id = upload(client, ws_a.id, [jpeg()], texts=[LETTER]).json()["id"]

    jobs.run_job(ws_a.id, document_id, attempts=0)

    with workspace_context(ws_a.id):
        assert Document.objects.get().status == "needs_review"


def test_bsn_never_in_ai_call(client: Client, ws_a: Workspace) -> None:
    client.force_login(admin_of(ws_a))
    document_id = upload(client, ws_a.id, [jpeg()], texts=[LETTER]).json()["id"]
    provider = QuotingProvider()

    run_pipeline(ws_a.id, document_id, provider_factory=lambda _: provider)

    assert provider.texts
    assert all(BSN not in text for text in provider.texts)


def test_bsn_nowhere_in_storage(
    client: Client, ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    # "BSN komt nergens voor in OCR-tekst, zoekindex, logs of AI-aanroepen."
    client.force_login(admin_of(ws_a))
    with caplog.at_level("DEBUG"):
        document_id = upload(client, ws_a.id, [jpeg()], texts=[LETTER]).json()["id"]
        run_pipeline(ws_a.id, document_id, provider_factory=lambda _: QuotingProvider())
        body = client.get(f"/api/v1/workspaces/{ws_a.id}/documents/{document_id}").content

    with workspace_context(ws_a.id):
        stored = [Document.objects.get().ocr_text]
        stored += [page.ocr_text for page in DocumentPage.objects.all()]
        proposals = list(Extraction.objects.all())
        stored += [p.value for p in proposals] + [p.quote for p in proposals]
        rows = list(DocumentSuggestion.objects.all())
        stored += [row.quote for row in rows] + [row.rule_name for row in rows]
    assert proposals
    # The preset rule "Label Zorg" recognised "CAK" and stored its quote.
    assert [(row.origin, row.rule_name, row.quote) for row in rows] == [
        ("rule", "Label Zorg", "CAK")
    ]
    assert all(BSN not in text for text in stored)
    assert BSN not in body.decode()
    assert BSN not in caplog.text


NAMED_LETTER = "T.a.v. J. de Vries\nBelastingdienst\nU moet betalen vóór 1 december 2026."


def add_person(workspace: Workspace) -> Person:
    with workspace_context(workspace.id):
        return Person.objects.create(
            workspace=workspace, display_name="Jan de Vries", name_variants=["J. de Vries"]
        )


class PickingProvider(QuotingProvider):
    """Keeps every request and picks Jan de Vries and the first label it is offered."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> str:
        self.requests.append(request)
        return json.dumps(
            {
                "afzender": {"naam": None, "bron": None, "zekerheid": 0},
                "documenttype": "overig",
                "dagtekening": {"waarde": None, "bron": None, "zekerheid": 0},
                "samenvatting_b1": "",
                "acties": [],
                "factuur": None,
                "op_naam_van": [
                    {"id": jan.alias, "bron": "J. de Vries", "zekerheid": 0.9}
                    for jan in request.persons
                    if jan.name == "Jan de Vries"
                ],
                "labels": [{"id": request.labels[0].alias, "zekerheid": 0.9}],
            }
        )


def suggested_name(row: DocumentSuggestion) -> str:
    if row.person is not None:
        return row.person.display_name
    return row.label.name if row.label is not None else ""


def test_request_carries_names_but_no_ids(client: Client, ws_a: Workspace) -> None:
    # Design 10.3: names of labels and persons go along; never their ids.
    add_person(ws_a)
    client.force_login(admin_of(ws_a))
    document_id = upload(client, ws_a.id, [jpeg()], texts=[NAMED_LETTER]).json()["id"]
    provider = PickingProvider()

    run_pipeline(ws_a.id, document_id, provider_factory=lambda _: provider)

    [request] = provider.requests
    sent = choices_block(request) + json.dumps(json_schema(request))
    with workspace_context(ws_a.id):
        ids = [str(i) for i in Label.objects.values_list("id", flat=True)]
        ids += [str(i) for i in Person.objects.values_list("id", flat=True)]
        label_names = list(Label.objects.values_list("name", flat=True))
        stored = sorted(
            (row.origin, suggested_name(row))
            for row in DocumentSuggestion.objects.filter(state="open").select_related(
                "person", "label"
            )
        )
    assert ids
    assert all(i not in sent for i in [*ids, str(document_id)])
    assert "Jan de Vries" in sent
    assert "J. de Vries" in sent
    assert all(name in sent for name in label_names)
    # The aliases came back and were turned into the workspace's own ids (the AI's pick
    # of Jan joins the stronger name match); the preset rule proposes "Belastingdienst".
    assert stored == [
        ("ai", request.labels[0].name),
        ("name_match", "Jan de Vries"),
        ("rule", "Belastingdienst"),
    ]


def test_ai_off_sends_nothing(
    client: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> httpx.Response:
        pytest.fail("A request left the server although AI is off.")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(OpenAICompatibleProvider, "extract", no_network)
    person = add_person(ws_a)
    client.force_login(admin_of(ws_a))
    document_id = upload(client, ws_a.id, [jpeg()], texts=[NAMED_LETTER]).json()["id"]

    jobs.run_job(ws_a.id, document_id, attempts=0)

    with workspace_context(ws_a.id):
        rows = {
            (row.person_id or row.label_id, row.origin) for row in DocumentSuggestion.objects.all()
        }
        belasting = Label.objects.get(system_key="belastingdienst")
    # The deterministic layer (name matching and the preset rules) still works without AI.
    assert rows == {(person.id, "name_match"), (belasting.id, "rule")}


def test_no_person_or_label_name_in_logs(
    client: Client, ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    add_person(ws_a)
    client.force_login(admin_of(ws_a))
    document_id = upload(client, ws_a.id, [jpeg()], texts=[NAMED_LETTER]).json()["id"]
    with workspace_context(ws_a.id):
        label_names = list(Label.objects.values_list("name", flat=True))

    with caplog.at_level("DEBUG"):
        run_pipeline(ws_a.id, document_id, provider_factory=lambda _: PickingProvider())

    assert "suggestions" in caplog.text
    assert "Vries" not in caplog.text
    assert all(name not in caplog.text for name in label_names)

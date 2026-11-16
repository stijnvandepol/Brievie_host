"""Acceptance criteria from spec section 16 about what leaves the server and BSNs."""

import json

import httpx
import pytest
from django.test import Client

from apps.documents.models import Document, DocumentPage, Extraction
from apps.pipeline import jobs
from apps.pipeline.choices import ExtractionRequest
from apps.pipeline.providers import OpenAICompatibleProvider
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
    assert proposals
    assert all(BSN not in text for text in stored)
    assert BSN not in body.decode()
    assert BSN not in caplog.text

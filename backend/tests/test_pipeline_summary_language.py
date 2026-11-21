"""The workspace's summary language reaches the prompt, and only two of its places."""

import uuid

from apps.documents.models import Document
from apps.pipeline.choices import ExtractionRequest
from apps.pipeline.providers import _PROMPT, system_prompt
from apps.pipeline.steps import run_pipeline
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Workspace
from tests.test_pipeline_steps import answer

DUTCH_SUMMARY = "in eenvoudig Nederlands (taalniveau B1), in de je-vorm."
DUTCH_DESCRIPTION = '"omschrijving" is één korte zin in de je-vorm.'


def test_the_dutch_prompt_is_the_one_from_before_the_setting() -> None:
    prompt = system_prompt("nl")

    assert DUTCH_SUMMARY in prompt
    assert DUTCH_DESCRIPTION in prompt
    assert "{{" not in prompt
    assert system_prompt() == prompt
    # A language it does not know falls back to Dutch.
    assert system_prompt("de") == prompt


def test_the_english_prompt_changes_only_the_summary_and_description_lines() -> None:
    dutch = system_prompt("nl").splitlines()
    english = system_prompt("en").splitlines()

    assert len(dutch) == len(english)
    changed = [i for i, (a, b) in enumerate(zip(dutch, english, strict=True)) if a != b]
    assert [dutch[i].split(":")[0] for i in changed] == ['- "samenvatting_b1"', '- "acties"']
    assert "eenvoudig Engels" in english[changed[0]]
    assert "eenvoudig Engels" in english[changed[1]]
    # Quotes and every other value still come from the letter as it is.
    assert 'elke "bron" neem je over zoals ze in de brief staan' in english[changed[0]]
    assert "{{" not in "\n".join(english)
    assert "{{samenvatting_taal}}" in _PROMPT.read_text(encoding="utf-8")


class RecordingProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.requests: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> str:
        self.requests.append(request)
        return answer()


def _run(workspace: Workspace) -> RecordingProvider:
    with workspace_context(workspace.id, actor=SYSTEM):
        document = Document.objects.create(
            workspace=workspace, client_id=uuid.uuid4(), page_count=1, ocr_text="Brief"
        )
    provider = RecordingProvider()
    run_pipeline(workspace.id, document.id, provider_factory=lambda _: provider)
    return provider


def test_the_request_carries_the_workspace_summary_language(ws_a: Workspace) -> None:
    assert _run(ws_a).requests[0].summary_language == "nl"

    Workspace.objects.filter(id=ws_a.id).update(summary_language="en")

    assert _run(ws_a).requests[0].summary_language == "en"

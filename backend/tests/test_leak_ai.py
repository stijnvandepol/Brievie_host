"""Nobody sends a colleague's letter to the AI (S 4.10, plan Task 22).

The AI service reads every letter it processes, under the workspace's recorded consent
(`PRIVACY.md`). What must not happen: a member causing a letter they cannot read to be
sent again, or one request carrying the text of other letters. The autopilot runs as
the system, and what it changes on a letter's addressees reaches `addressee_ids` (and so
who sees the letter) in the same transaction.
"""

import json
import uuid
from datetime import date

import pytest
from django.test import Client
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.documents.models import Document, DocumentAddressee
from apps.people.models import Person
from apps.pipeline import settings_service
from apps.pipeline.choices import ExtractionRequest
from apps.pipeline.steps import run_pipeline
from apps.workspaces.context import SYSTEM
from tests.documents_support import documents_url
from tests.visibility_support import Office, as_actor, office

pytestmark = pytest.mark.usefixtures("without_preset_rules")

TODAY = date(2026, 11, 14)
# One word per letter, found in no other letter.
WORDS = {
    "own_piet": "appelboom",
    "finance_letter": "bergkristal",
    "hr_letter": "citroenpers",
    "unassigned": "dakgoot",
    "only_directie": "eikenhout",
    "person_without_account": "fietsbel",
    "scanned_in_review": "gieter",
    "private_femke": "hangmat",
}


class Recording:
    """A provider that records every request and proposes nothing."""

    name = "fake"
    model = "fake-1"

    def __init__(self) -> None:
        self.requests: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> str:
        self.requests.append(request)
        return json.dumps(
            {
                "afzender": {"naam": "", "bron": "", "zekerheid": 0.0},
                "documenttype": "",
                "dagtekening": {"waarde": None, "bron": None, "zekerheid": 0.0},
                "samenvatting_b1": "",
                "acties": [],
                "factuur": None,
            }
        )


def _process(o: Office, document_id: uuid.UUID, provider: Recording) -> None:
    run_pipeline(
        o.workspace.id, document_id, provider_factory=lambda _: provider, today=lambda: TODAY
    )


def _texts(o: Office) -> None:
    with as_actor(o.workspace, SYSTEM):
        for name, word in WORDS.items():
            Document.objects.filter(id=o.letters[name].id).update(
                ocr_text=f"Brief over {word}, met vriendelijke groet."
            )


def _process_jobs() -> int:
    return ProcrastinateJob.objects.filter(task_name="process_document").count()


def test_retry_by_a_member_who_cannot_read_the_letter_is_404_and_sends_nothing(
    client: Client,
) -> None:
    o = office()
    letter = o.letters["finance_letter"]
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=letter.id).update(status=Document.Status.FAILED)
    jobs = _process_jobs()
    client.force_login(o.hr)

    hidden = client.post(f"{documents_url(o.workspace.id)}/{letter.id}/retry")
    unknown = client.post(f"{documents_url(o.workspace.id)}/{uuid.uuid4()}/retry")

    assert hidden.status_code == 404
    assert hidden.json() == unknown.json()
    assert _process_jobs() == jobs
    with as_actor(o.workspace, SYSTEM):
        assert Document.objects.get(id=letter.id).status == Document.Status.FAILED

    client.force_login(o.finance)
    assert client.post(f"{documents_url(o.workspace.id)}/{letter.id}/retry").status_code == 202


def test_ai_request_holds_only_its_own_letter() -> None:
    o = office()
    _texts(o)
    letter = o.letters["finance_letter"]
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id=letter.id).update(status=Document.Status.PROCESSING)
    provider = Recording()

    _process(o, letter.id, provider)

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert WORDS["finance_letter"] in request.text
    sent = repr(request)
    for name, word in WORDS.items():
        if name != "finance_letter":
            assert word not in sent, name


def test_autopilot_runs_as_system_and_syncs() -> None:
    o = office()
    with as_actor(o.workspace, SYSTEM):
        person = Person.objects.create(workspace=o.workspace, display_name="Jan de Vries")
        settings_service.configure(
            workspace_id=o.workspace.id,
            mode="external",
            supplier="mistral",
            base_url="",
            model="",
            api_key="nep-sleutel-voor-tests",
            consent=True,
        )
        settings_service.configure_autopilot(
            workspace_id=o.workspace.id,
            organise=True,
            confirm=False,
            acknowledged=True,
            actor=settings_service.Actor(user_id=None, label="api"),
        )
        document = Document.objects.create(
            workspace=o.workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            ocr_text="Geachte Jan de Vries,\nUw afspraak bij de huisarts is verzet.",
            status=Document.Status.PROCESSING,
        )

    _process(o, document.id, Recording())

    with as_actor(o.workspace, SYSTEM):
        stored = Document.objects.get(id=document.id)
        linked = sorted(
            DocumentAddressee.objects.filter(document=document).values_list("person_id", flat=True)
        )
    assert person.id in linked
    assert sorted(stored.addressee_ids) == linked
    # Jan de Vries has no account: the letter goes to the sorter, not to Hans.
    with as_actor(o.workspace, o.sorter.id):
        assert Document.objects.filter(id=document.id).exists()
    with as_actor(o.workspace, o.hr.id):
        assert not Document.objects.filter(id=document.id).exists()

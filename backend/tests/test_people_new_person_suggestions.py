"""The AI may propose a person nobody knows yet (Mensen, Besluit B3), as a proposal only:
nothing makes the person but a member's yes, a dismissed name never comes back on the
letter, and the proposal is seen only with its letter."""

import copy
import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.test import Client

from apps.audit.models import WorkspaceAuditEvent
from apps.documents.models import Document, DocumentAddressee, NewPersonSuggestion
from apps.people.models import Person
from apps.pipeline import settings_service
from apps.pipeline.choices import ExtractionRequest
from apps.pipeline.schema import ExtractionPayload
from apps.pipeline.steps import run_pipeline
from apps.pipeline.validation import CHOICE_CONFIDENCE_CAP, MAX_NEW_NAMES, validate_new_names
from apps.workspaces.context import SYSTEM, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, make_person
from tests.helpers import post_json
from tests.members_support import as_user, join

pytestmark = pytest.mark.usefixtures("without_preset_rules")

TODAY = date(2026, 11, 14)
TEXT = (
    "Belastingdienst\nDatum 3 november 2026\n"
    "Aan M. Jansen. Uw aanslag is vastgesteld. Betaal vóór 1 december 2026."
)
ANSWER: dict[str, object] = {
    "afzender": {"naam": "Belastingdienst", "bron": "Belastingdienst", "zekerheid": 0.95},
    "documenttype": "aanslag",
    "dagtekening": {"waarde": "2026-11-03", "bron": "Datum 3 november 2026", "zekerheid": 0.95},
    "samenvatting_b1": "Je moet de aanslag betalen.",
    "acties": [
        {
            "type": "betalen",
            "omschrijving": "Betaal de aanslag",
            "deadline": {"waarde": "2026-12-01", "bron": "vóór 1 december 2026", "zekerheid": 0.95},
        }
    ],
    "factuur": None,
    "nieuwe_personen": [{"naam": "M. Jansen", "bron": "Aan M. Jansen", "zekerheid": 0.9}],
}


class Answering:
    name = "fake"
    model = "fake-1"

    def __init__(self, answer: dict[str, object] | None = None) -> None:
        self.answer = copy.deepcopy(answer if answer is not None else ANSWER)

    def extract(self, request: ExtractionRequest) -> str:
        body = dict(self.answer)
        body["op_naam_van"] = []
        body["labels"] = []
        return json.dumps(body)


def _letter(workspace: Workspace, text: str = TEXT) -> Document:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            ocr_text=text,
            status=Document.Status.PROCESSING,
        )


def _run(workspace: Workspace, document: Document, provider: Answering | None = None) -> None:
    run_pipeline(
        workspace.id,
        document.id,
        provider_factory=lambda _: provider or Answering(),
        today=lambda: TODAY,
    )


def _open(workspace: Workspace, document: Document) -> list[NewPersonSuggestion]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return list(
            NewPersonSuggestion.objects.filter(
                document=document, state=NewPersonSuggestion.State.OPEN
            )
        )


def _payload(names: list[dict[str, object]]) -> ExtractionPayload:
    return ExtractionPayload.model_validate({**ANSWER, "nieuwe_personen": names})


# --- validation (pure) ---


def test_only_names_the_letter_holds_without_a_bsn_at_most_three() -> None:
    text = "Aan M. Jansen, Piet de Boer, An, Joke Smit en Kees Bos. BSN 111222333."
    names = validate_new_names(
        _payload(
            [
                {"naam": "M. Jansen", "bron": "Aan M. Jansen", "zekerheid": 0.99},
                {"naam": "m.  jansen", "bron": None, "zekerheid": 0.5},  # the same name
                {"naam": "Verzonnen Naam", "bron": "Aan M. Jansen", "zekerheid": 0.9},
                {"naam": "An", "bron": None, "zekerheid": 0.9},
                {"naam": "111222333", "bron": None, "zekerheid": 0.9},
                {"naam": "Piet de Boer", "bron": "elders", "zekerheid": 0.4},
                {"naam": "Joke Smit", "bron": None, "zekerheid": 0.4},
                {"naam": "Kees Bos", "bron": None, "zekerheid": 0.4},
            ]
        ),
        text,
    )
    assert [n.name for n in names] == ["M. Jansen", "An", "Piet de Boer"][:MAX_NEW_NAMES]
    assert names[0].confidence == CHOICE_CONFIDENCE_CAP
    assert names[0].key == "m. jansen"
    assert names[2].quote == ""  # a quote the letter does not hold is not kept


def test_a_masked_name_is_never_proposed() -> None:
    names = validate_new_names(
        _payload([{"naam": "[BSN] Jansen", "bron": None, "zekerheid": 0.9}]),
        "Aan [BSN] Jansen",
    )
    assert names == []


# --- the pipeline ---


def test_an_unknown_name_becomes_a_proposal_and_nobody_is_made(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        persons = Person.objects.count()
    document = _letter(ws_a)
    _run(ws_a, document)
    (row,) = _open(ws_a, document)
    assert (row.name, row.quote, row.confidence) == ("M. Jansen", "Aan M. Jansen", Decimal("0.800"))
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Person.objects.count() == persons


@pytest.mark.parametrize("archived", [False, True])
def test_a_name_somebody_has_is_not_proposed(ws_a: Workspace, archived: bool) -> None:
    make_person(ws_a, "M. Jansen", archived=archived)
    document = _letter(ws_a)
    _run(ws_a, document)
    assert _open(ws_a, document) == []


def test_a_name_beyond_the_fifty_sent_to_the_ai_is_known_too(ws_a: Workspace) -> None:
    for number in range(55):
        make_person(ws_a, f"Aa {number:02d}")
    with workspace_context(ws_a.id, actor=SYSTEM):
        Person.objects.create(workspace=ws_a, display_name="Zz", name_variants=["M. Jansen"])
    document = _letter(ws_a)
    _run(ws_a, document)
    assert _open(ws_a, document) == []


def test_a_dismissed_name_is_never_proposed_again_on_that_letter(ws_a: Workspace) -> None:
    document = _letter(ws_a)
    _run(ws_a, document)
    (row,) = _open(ws_a, document)
    client = as_user(admin_of(ws_a))
    base = f"/workspaces/{ws_a.id}/documents/{document.id}/new-person-suggestions/{row.id}"
    assert post_json(client, f"{base}/dismiss").status_code == 200
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(status=Document.Status.PROCESSING)
    _run(ws_a, document)
    assert _open(ws_a, document) == []


def test_the_autopilot_never_makes_the_person_and_does_not_confirm(ws_a: Workspace) -> None:
    with workspace_context(ws_a.id, actor=SYSTEM):
        settings_service.configure(
            workspace_id=ws_a.id,
            mode="external",
            supplier="mistral",
            base_url="",
            model="",
            api_key="nep-sleutel-voor-tests",
            consent=True,
        )
        settings_service.configure_autopilot(
            workspace_id=ws_a.id,
            organise=True,
            confirm=True,
            acknowledged=True,
            actor=settings_service.Actor(user_id=None, label="api"),
        )
        persons = Person.objects.count()
    document = _letter(ws_a)
    _run(ws_a, document)
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Document.objects.get(id=document.id).status == Document.Status.NEEDS_REVIEW
        assert Person.objects.count() == persons
    assert len(_open(ws_a, document)) == 1
    # Dismissed: the guard rails hold again, and the next run confirms it.
    (row,) = _open(ws_a, document)
    client = as_user(admin_of(ws_a))
    base = f"/workspaces/{ws_a.id}/documents/{document.id}/new-person-suggestions/{row.id}"
    assert post_json(client, f"{base}/dismiss").status_code == 200
    with workspace_context(ws_a.id, actor=SYSTEM):
        Document.objects.filter(id=document.id).update(status=Document.Status.PROCESSING)
    _run(ws_a, document)
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Document.objects.get(id=document.id).status == Document.Status.DONE
        assert Person.objects.count() == persons


# --- accepting and dismissing ---


def _proposed(ws: Workspace) -> tuple[Document, NewPersonSuggestion, str]:
    document = _letter(ws)
    _run(ws, document)
    (row,) = _open(ws, document)
    return (
        document,
        row,
        f"/workspaces/{ws.id}/documents/{document.id}/new-person-suggestions/{row.id}",
    )


def test_accepting_makes_the_person_and_puts_the_letter_on_them(ws_a: Workspace) -> None:
    document, row, base = _proposed(ws_a)
    member = join(ws_a)
    client = as_user(member)
    detail = client.get(f"/api/v1/workspaces/{ws_a.id}/documents/{document.id}").json()
    assert [s["name"] for s in detail["new_person_suggestions"]] == ["M. Jansen"]

    r = post_json(client, f"{base}/accept")
    assert r.status_code == 200, r.json()
    assert r.json()["new_person_suggestions"] == []
    with workspace_context(ws_a.id, actor=SYSTEM):
        person = Person.objects.get(display_name="M. Jansen")
        assert DocumentAddressee.objects.filter(document=document, person=person).exists()
        (audit,) = WorkspaceAuditEvent.objects.filter(code="person_created")
        assert (audit.actor_id, audit.target_id) == (member.id, person.id)
        assert NewPersonSuggestion.objects.get(id=row.id).person_id == person.id
    # Again: nothing changes.
    assert post_json(client, f"{base}/accept").status_code == 200
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert Person.objects.filter(display_name="M. Jansen").count() == 1


def test_accepting_after_the_name_was_added_takes_that_person(ws_a: Workspace) -> None:
    _document, _row, base = _proposed(ws_a)
    existing = make_person(ws_a, "m. jansen")
    r = post_json(as_user(admin_of(ws_a)), f"{base}/accept")
    assert r.status_code == 200, r.json()
    assert r.json()["addressee_ids"] == [str(existing.id)]


def test_a_viewer_cannot_accept_or_dismiss(ws_a: Workspace) -> None:
    _document, _row, base = _proposed(ws_a)
    viewer = as_user(join(ws_a, Membership.Role.VIEWER))
    for step in ("accept", "dismiss"):
        r = post_json(viewer, f"{base}/{step}")
        assert (r.status_code, r.json()["code"]) == (403, "forbidden")


def test_somebody_who_cannot_see_the_letter_learns_nothing(ws_b: Workspace) -> None:
    """Organisatie, own post: a member who does not see the letter gets the answer for a
    missing letter on both actions and sees no proposal."""
    document, row, base = _proposed(ws_b)
    stranger = join(ws_b)
    client = as_user(stranger)
    for step in ("accept", "dismiss"):
        r = post_json(client, f"{base}/{step}")
        assert (r.status_code, r.json()["code"]) == (404, "document_not_found")
    with workspace_context(ws_b.id, actor=stranger.id):
        assert not NewPersonSuggestion.objects.filter(id=row.id).exists()
    assert len(_open(ws_b, document)) == 1


def test_a_device_token_may_answer_like_the_other_proposals(ws_a: Workspace) -> None:
    """As `acceptSuggestion`: the app may later offer it; no session needed."""
    from tests.documents_support import bearer_for

    _document, _row, base = _proposed(ws_a)
    r = Client().post(
        f"/api/v1{base}/dismiss",
        content_type="application/json",
        headers=bearer_for(admin_of(ws_a)),
    )
    assert r.status_code == 200, r.json()

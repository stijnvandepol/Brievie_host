"""The autopilot's confirm switch: a letter is confirmed when every guard rail holds."""

import copy
import json
import logging
import uuid
from datetime import date
from decimal import Decimal

import pytest
from django.test import override_settings

from apps.common.errors import ApiError
from apps.documents import review, suggestions
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.documents.schemas import ConfirmIn, TaskIn
from apps.labels.models import Label
from apps.pipeline import settings_service, steps
from apps.pipeline.choices import ExtractionRequest
from apps.pipeline.steps import run_pipeline
from apps.tasks.models import Task
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of, make_person
from tests.rules_support import add_label, make_rule, sender_contains

pytestmark = pytest.mark.usefixtures("without_preset_rules")

TODAY = date(2026, 11, 14)
TEXT = (
    "Belastingdienst\nDatum 3 november 2026\n"
    "Uw aanslag is vastgesteld. Jan de Vries moet betalen vóór 1 december 2026."
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
}


class Answering:
    """A provider that answers the same payload, picking labels by name when told to."""

    name = "fake"
    model = "fake-1"

    def __init__(
        self, answer: dict[str, object] | None = None, labels: dict[str, float] | None = None
    ) -> None:
        self.answer = copy.deepcopy(answer if answer is not None else ANSWER)
        self.labels = labels or {}

    def extract(self, request: ExtractionRequest) -> str:
        body = dict(self.answer)
        body["op_naam_van"] = []
        body["labels"] = [
            {"id": c.alias, "zekerheid": self.labels[c.name]}
            for c in request.labels
            if c.name in self.labels
        ]
        return json.dumps(body)


def switch_on(workspace: Workspace, *, organise: bool = False, confirm: bool = True) -> None:
    with workspace_context(workspace.id):
        settings_service.configure(
            workspace_id=workspace.id,
            mode="external",
            supplier="mistral",
            base_url="",
            model="",
            api_key="nep-sleutel-voor-tests",
            consent=True,
        )
        settings_service.configure_autopilot(
            workspace_id=workspace.id,
            organise=organise,
            confirm=confirm,
            acknowledged=True,
            actor=settings_service.Actor(user_id=None, label="api"),
        )


def new_document(workspace: Workspace, text: str = TEXT) -> Document:
    with workspace_context(workspace.id):
        return Document.objects.create(
            workspace=workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            ocr_text=text,
            status=Document.Status.PROCESSING,
        )


def run(workspace: Workspace, document: Document, provider: Answering | None) -> None:
    run_pipeline(
        workspace.id, document.id, provider_factory=lambda _: provider, today=lambda: TODAY
    )


def stored(workspace: Workspace, document: Document) -> Document:
    with workspace_context(workspace.id):
        return Document.objects.get(id=document.id)


def test_switch_off_leaves_the_letter_for_review(ws_a: Workspace) -> None:
    switch_on(ws_a, confirm=False)
    document = new_document(ws_a)

    run(ws_a, document, Answering())

    assert stored(ws_a, document).status == Document.Status.NEEDS_REVIEW


def test_when_every_rail_holds_the_letter_is_confirmed_by_the_ai(ws_a: Workspace) -> None:
    switch_on(ws_a)
    document = new_document(ws_a)

    run(ws_a, document, Answering())

    done = stored(ws_a, document)
    assert done.status == Document.Status.DONE
    assert (done.confirmed_by, done.confirmed_by_ai, done.confirmed_organisation) == (
        None,
        True,
        None,
    )
    assert done.confirmed_at is not None
    assert (done.sender, done.document_type, done.letter_date) == (
        "Belastingdienst",
        "assessment",
        date(2026, 11, 3),
    )
    assert (done.summary, done.summary_by_ai) == ("Je moet de aanslag betalen.", True)
    with workspace_context(ws_a.id):
        (task,) = Task.objects.filter(document=done)
        events = DocumentEvent.objects.filter(document=done, kind="confirmed")
        (event,) = events
    assert (task.created_by_ai, task.deadline, task.deadline_source) == (
        True,
        date(2026, 12, 1),
        "in_document",
    )
    assert task.deadline_quote == "vóór 1 december 2026"
    assert (event.origin, event.actor) == ("ai", None)


def test_the_rules_of_trigger_confirmed_run(ws_a: Workspace) -> None:
    switch_on(ws_a)
    with workspace_context(ws_a.id):
        label = Label.objects.create(workspace=ws_a, name="Belasting")
    make_rule(ws_a, sender_contains("Belastingdienst"), actions=[add_label(label)], mode="auto")
    document = new_document(ws_a)

    run(ws_a, document, Answering())

    assert stored(ws_a, document).status == Document.Status.DONE
    with workspace_context(ws_a.id):
        assert DocumentLabel.objects.filter(document=document, label=label).exists()


def test_a_failing_rail_leaves_the_letter_for_review_with_its_proposals(ws_a: Workspace) -> None:
    switch_on(ws_a)
    answer = copy.deepcopy(ANSWER)
    answer["afzender"] = {"naam": "Belastingdienst", "bron": "Belastingdienst", "zekerheid": 0.8}
    document = new_document(ws_a)

    run(ws_a, document, Answering(answer))

    assert stored(ws_a, document).status == Document.Status.NEEDS_REVIEW
    with workspace_context(ws_a.id):
        assert document.extractions.count() > 0
        assert not Task.objects.filter(document=document).exists()


def test_both_switches_on_with_an_open_proposal_left_waits_for_review(ws_a: Workspace) -> None:
    switch_on(ws_a, organise=True)
    with workspace_context(ws_a.id):
        Label.objects.create(workspace=ws_a, name="Gezondheid")
    document = new_document(ws_a)

    # Below AUTO_APPLY_FROM, so the proposal stays open after the organise step.
    run(ws_a, document, Answering(labels={"Gezondheid": 0.7}))

    assert stored(ws_a, document).status == Document.Status.NEEDS_REVIEW
    with workspace_context(ws_a.id):
        assert DocumentSuggestion.objects.filter(document=document, state="open").count() == 1


def test_an_open_proposal_does_not_block_when_organise_is_off(ws_a: Workspace) -> None:
    switch_on(ws_a, organise=False)
    with workspace_context(ws_a.id):
        Label.objects.create(workspace=ws_a, name="Gezondheid")
    document = new_document(ws_a)

    run(ws_a, document, Answering(labels={"Gezondheid": 0.7}))

    assert stored(ws_a, document).status == Document.Status.DONE
    with workspace_context(ws_a.id):
        assert DocumentSuggestion.objects.filter(document=document, state="open").count() == 1


def test_both_switches_on_and_nothing_left_open_confirms(ws_a: Workspace) -> None:
    switch_on(ws_a, organise=True)
    with workspace_context(ws_a.id):
        Label.objects.create(workspace=ws_a, name="Gezondheid")
    make_person(ws_a, "Jan de Vries")
    document = new_document(ws_a)

    run(ws_a, document, Answering(labels={"Gezondheid": 0.9}))

    assert stored(ws_a, document).status == Document.Status.DONE


def test_the_server_switch_off_leaves_the_letter_for_review(ws_a: Workspace) -> None:
    switch_on(ws_a)
    document = new_document(ws_a)

    with override_settings(BRIEVIE_AI_AUTOPILOT_ALLOWED=False):
        run(ws_a, document, Answering())

    assert stored(ws_a, document).status == Document.Status.NEEDS_REVIEW


def test_without_an_ai_answer_nothing_is_confirmed(ws_a: Workspace) -> None:
    switch_on(ws_a)
    document = new_document(ws_a)

    run(ws_a, document, None)

    assert stored(ws_a, document).status == Document.Status.NEEDS_REVIEW


def test_a_deadline_the_letter_does_not_hold_stays_a_proposal(ws_a: Workspace) -> None:
    # The quote is not in the stored text, so validation gives the deadline confidence 0.
    switch_on(ws_a)
    answer = copy.deepcopy(ANSWER)
    action = answer["acties"][0]  # type: ignore[index]
    action["deadline"]["bron"] = "vóór 2 december 2026"
    document = new_document(ws_a)

    run(ws_a, document, Answering(answer))

    assert stored(ws_a, document).status == Document.Status.NEEDS_REVIEW


def test_a_failure_while_confirming_rolls_back_and_leaves_the_letter_for_review(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    switch_on(ws_a)
    document = new_document(ws_a)

    def broken(document: Document, data: ConfirmIn) -> None:
        document.sender = "half geschreven"
        document.save()
        raise RuntimeError("Belastingdienst Jan de Vries")

    monkeypatch.setattr(review, "confirm_automatically", broken)
    with caplog.at_level(logging.INFO):
        run(ws_a, document, Answering())

    after = stored(ws_a, document)
    assert (after.status, after.sender, after.confirmed_by_ai) == ("needs_review", "", False)
    assert "RuntimeError" in caplog.text
    assert "Jan de Vries" not in caplog.text


def test_the_log_line_holds_no_letter_content(
    ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    switch_on(ws_a)
    document = new_document(ws_a)

    with caplog.at_level(logging.INFO):
        run(ws_a, document, Answering())

    assert "Belastingdienst" not in caplog.text
    assert "aanslag" not in caplog.text.lower()


def test_a_persons_confirm_after_a_reopen_leaves_confirmed_by_ai_false(
    ws_a: Workspace,
) -> None:
    from apps.documents import services

    switch_on(ws_a)
    document = new_document(ws_a)
    run(ws_a, document, Answering())
    admin = admin_of(ws_a)
    with workspace_context(ws_a.id):
        services.reopen_document(workspace=ws_a, user=admin, document_id=document.id)
        review.confirm_document(
            workspace=ws_a,
            user=admin,
            document_id=document.id,
            data=ConfirmIn(
                sender="Belastingdienst",
                document_type="assessment",
                letter_date=date(2026, 11, 3),
                summary="",
                summary_by_ai=False,
                tasks=[],
            ),
        )
    done = stored(ws_a, document)
    assert (done.status, done.confirmed_by, done.confirmed_by_ai) == ("done", admin, False)


def test_a_persons_confirm_clears_a_stale_ai_flag(ws_a: Workspace) -> None:
    document = new_document(ws_a)
    with workspace_context(ws_a.id):
        Document.objects.filter(id=document.id).update(status="needs_review", confirmed_by_ai=True)
        review.confirm_document(
            workspace=ws_a,
            user=admin_of(ws_a),
            document_id=document.id,
            data=ConfirmIn(
                sender="Belastingdienst",
                document_type="assessment",
                letter_date=date(2026, 11, 3),
                summary="",
                summary_by_ai=False,
                tasks=[],
            ),
        )
    assert stored(ws_a, document).confirmed_by_ai is False


def test_confirm_automatically_validates_a_deadline_quote_against_the_letter(
    ws_a: Workspace,
) -> None:
    from apps.common.errors import ApiError
    from apps.documents.schemas import TaskIn

    document = new_document(ws_a, "Een brief zonder datum.")
    data = ConfirmIn(
        sender="X",
        document_type="other",
        letter_date=date(2026, 11, 3),
        summary="",
        summary_by_ai=True,
        tasks=[
            TaskIn(
                type="pay",
                description="Betaal",
                deadline=date(2026, 12, 1),
                deadline_source="in_document",
                deadline_quote="vóór 1 december 2026",
            )
        ],
    )
    with workspace_context(ws_a.id), pytest.raises(ApiError):
        review.confirm_automatically(document, data)


def test_a_failure_in_the_decision_leaves_the_letter_for_review_not_processing(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    switch_on(ws_a)
    document = new_document(ws_a)

    def broken(**kwargs: object) -> None:
        raise ValueError("Belastingdienst Jan de Vries")

    monkeypatch.setattr(steps, "decide_confirmation", broken)
    with caplog.at_level(logging.INFO):
        run(ws_a, document, Answering())

    assert stored(ws_a, document).status == Document.Status.NEEDS_REVIEW
    with workspace_context(ws_a.id):
        assert document.extractions.count() > 0
        assert not Task.objects.filter(document=document).exists()
    line = next(r.getMessage() for r in caplog.records if "while confirming" in r.getMessage())
    assert line == f"Unexpected ValueError while confirming document {document.id}"
    assert "Jan de Vries" not in caplog.text


def test_when_applying_the_picks_failed_the_letter_is_not_confirmed(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No label or person to pick: without the step nothing is open, so the "nothing
    # open" rail alone would pass vacuously.
    switch_on(ws_a, organise=True)
    document = new_document(ws_a)

    def broken(document: Document) -> int:
        raise RuntimeError("stuk")

    monkeypatch.setattr(steps, "apply_open_proposals", broken)
    run(ws_a, document, Answering())

    assert stored(ws_a, document).status == Document.Status.NEEDS_REVIEW


def broken_organise(document: Document, text: str, persons: object, ai_choices: object) -> None:
    raise RuntimeError("Jan de Vries")


@pytest.mark.parametrize("organise_on", [False, True])
def test_a_failed_organise_step_stops_the_confirm_whatever_the_switches(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch, organise_on: bool
) -> None:
    switch_on(ws_a, organise=organise_on)
    document = new_document(ws_a)
    monkeypatch.setattr(steps, "organise_processed", broken_organise)

    run(ws_a, document, Answering())

    after = stored(ws_a, document)
    assert (after.status, after.confirmed_by_ai) == ("needs_review", False)
    with workspace_context(ws_a.id):
        assert not Task.objects.filter(document=document).exists()


def test_a_failed_organise_step_stops_applying_proposals_left_open(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A retry after `mark_failed`: a strong name match from the first run is still open.
    switch_on(ws_a, organise=True)
    person = make_person(ws_a, "Jan de Vries")
    document = new_document(ws_a)
    with workspace_context(ws_a.id):
        locked = Document.objects.select_for_update().get(id=document.id)
        suggestions.propose(
            locked,
            action=DocumentSuggestion.Action.ADD_ADDRESSEE,
            person=person,
            origin=DocumentSuggestion.Origin.NAME_MATCH,
            quote="Jan de Vries",
            confidence=Decimal("0.950"),
            preselected=True,
        )
    applied: list[object] = []

    def counting(locked: Document) -> int:
        applied.append(locked.id)
        return 0

    monkeypatch.setattr(steps, "organise_processed", broken_organise)
    monkeypatch.setattr(steps, "apply_open_proposals", counting)

    run(ws_a, document, Answering())

    assert applied == []
    after = stored(ws_a, document)
    assert (after.status, after.confirmed_by_ai) == ("needs_review", False)
    with workspace_context(ws_a.id):
        assert not DocumentAddressee.objects.filter(document=document).exists()
        assert DocumentSuggestion.objects.get(document=document).state == "open"


def test_a_refused_validation_is_logged_at_info_with_the_id_only(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    switch_on(ws_a)
    document = new_document(ws_a)

    def refused(document: Document, data: ConfirmIn) -> None:
        raise ApiError(422, "validation_error", "Jan de Vries")

    monkeypatch.setattr(review, "confirm_automatically", refused)
    with caplog.at_level(logging.INFO):
        run(ws_a, document, Answering())

    assert stored(ws_a, document).status == Document.Status.NEEDS_REVIEW
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    line = next(r for r in caplog.records if "not confirmed" in r.getMessage())
    assert line.levelno == logging.INFO
    assert line.getMessage() == (
        f"Document {document.id} not confirmed by AI: the review did not pass validation"
    )
    assert "Jan de Vries" not in caplog.text


def test_the_rules_of_trigger_confirmed_run_exactly_once(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.rules import triggers

    calls: list[object] = []
    original = triggers.on_confirmed

    def counting(document: Document) -> None:
        calls.append(document.id)
        original(document)

    monkeypatch.setattr(triggers, "on_confirmed", counting)
    switch_on(ws_a)
    document = new_document(ws_a)

    run(ws_a, document, Answering())

    assert stored(ws_a, document).status == Document.Status.DONE
    assert calls == [document.id]


def test_confirm_automatically_never_sets_the_organisation(ws_a: Workspace) -> None:
    document = new_document(ws_a)
    with workspace_context(ws_a.id):
        label = Label.objects.create(workspace=ws_a, name="Belasting")
        review.confirm_automatically(
            document,
            ConfirmIn(
                sender="Belastingdienst",
                document_type="assessment",
                letter_date=date(2026, 11, 3),
                summary="",
                summary_by_ai=True,
                tasks=[],
                sets_organisation=True,
                label_ids=[label.id],
            ),
        )
        assert not DocumentLabel.objects.filter(document=document).exists()


def test_a_task_the_person_ticked_off_is_not_created_again_after_a_reopen(
    ws_a: Workspace,
) -> None:
    from apps.documents import services

    switch_on(ws_a)
    document = new_document(ws_a)
    run(ws_a, document, Answering())
    admin = admin_of(ws_a)
    with workspace_context(ws_a.id):
        Task.objects.filter(document=document).update(status=Task.Status.DONE)
        services.reopen_document(workspace=ws_a, user=admin, document_id=document.id)
        review.confirm_document(
            workspace=ws_a,
            user=admin,
            document_id=document.id,
            data=ConfirmIn(
                sender="Belastingdienst",
                document_type="assessment",
                letter_date=date(2026, 11, 3),
                summary="",
                summary_by_ai=False,
                tasks=[
                    TaskIn(
                        type="pay",
                        description="Betaal de aanslag",
                        deadline=date(2026, 12, 1),
                        deadline_source="in_document",
                        deadline_quote="vóór 1 december 2026",
                    ),
                    TaskIn(
                        type="respond",
                        description="Bel de Belastingdienst",
                        deadline_source="manual",
                    ),
                ],
            ),
        )
        tasks = list(Task.objects.filter(document=document).order_by("description"))
    assert [(t.description, t.status, t.created_by_ai) for t in tasks] == [
        ("Bel de Belastingdienst", "open", False),
        ("Betaal de aanslag", "done", True),
    ]


def _organised_by_ai(workspace: Workspace) -> tuple[Document, Label, object]:
    """A letter the AI organised and confirmed: label Gezondheid and Jan de Vries, automatic."""
    switch_on(workspace, organise=True)
    with workspace_context(workspace.id):
        label = Label.objects.create(workspace=workspace, name="Gezondheid")
    person = make_person(workspace, "Jan de Vries")
    document = new_document(workspace)
    run(workspace, document, Answering(labels={"Gezondheid": 0.9}))
    assert stored(workspace, document).confirmed_by_ai is True
    with workspace_context(workspace.id):
        assert DocumentLabel.objects.get(document=document).automatic is True
        assert DocumentAddressee.objects.get(document=document).automatic is True
    return document, label, person


def _person_review(label: Label, person: object, *, sets_organisation: bool) -> ConfirmIn:
    return ConfirmIn(
        sender="Belastingdienst",
        document_type="assessment",
        letter_date=date(2026, 11, 3),
        summary="",
        summary_by_ai=False,
        tasks=[],
        sets_organisation=sets_organisation,
        label_ids=[label.id] if sets_organisation else [],
        addressee_ids=[person.id] if sets_organisation else [],  # type: ignore[attr-defined]
    )


def _undoable(workspace: Workspace, document: Document) -> dict[str, bool]:
    from apps.documents import services
    from apps.documents.schemas import DocumentEventOut

    with workspace_context(workspace.id):
        events = services.list_document_events(workspace=workspace, document_id=document.id)
    return {
        e.kind: DocumentEventOut.resolve_can_undo(e)
        for e in events
        if e.kind in ("label_added", "addressee_added")
    }


def test_a_persons_confirm_with_the_organisation_takes_the_automatic_links_over(
    ws_a: Workspace,
) -> None:
    from apps.documents import services

    document, label, person = _organised_by_ai(ws_a)
    assert _undoable(ws_a, document) == {"label_added": True, "addressee_added": True}
    admin = admin_of(ws_a)
    with workspace_context(ws_a.id):
        services.reopen_document(workspace=ws_a, user=admin, document_id=document.id)
        review.confirm_document(
            workspace=ws_a,
            user=admin,
            document_id=document.id,
            data=_person_review(label, person, sets_organisation=True),
        )
        assert DocumentLabel.objects.get(document=document).automatic is False
        assert DocumentAddressee.objects.get(document=document).automatic is False
    assert _undoable(ws_a, document) == {"label_added": False, "addressee_added": False}
    with workspace_context(ws_a.id):
        event = DocumentEvent.objects.get(document=document, kind="label_added")
        with pytest.raises(ApiError) as refused:
            services.undo_document_event(
                workspace=ws_a, user=admin, document_id=document.id, event_id=event.id
            )
    assert (refused.value.status, refused.value.code) == (409, "event_not_undoable")


def test_a_hand_edit_leaves_the_flag_of_kept_links_alone(ws_a: Workspace) -> None:
    from apps.documents import services

    document, label, person = _organised_by_ai(ws_a)
    admin = admin_of(ws_a)
    with workspace_context(ws_a.id):
        services.set_document_labels(
            workspace=ws_a, user=admin, document_id=document.id, label_ids=[label.id]
        )
        services.set_document_addressees(
            workspace=ws_a,
            user=admin,
            document_id=document.id,
            person_ids=[person.id],  # type: ignore[attr-defined]
        )
        assert DocumentLabel.objects.get(document=document).automatic is True
        assert DocumentAddressee.objects.get(document=document).automatic is True
    assert _undoable(ws_a, document) == {"label_added": True, "addressee_added": True}


def test_a_persons_review_of_an_ai_confirmed_letter_is_no_repeat(ws_a: Workspace) -> None:
    # The same review the AI stored, sent by a person: a conflict, as for any other review.
    switch_on(ws_a)
    document = new_document(ws_a)
    run(ws_a, document, Answering())
    with workspace_context(ws_a.id), pytest.raises(ApiError) as refused:
        review.confirm_document(
            workspace=ws_a,
            user=admin_of(ws_a),
            document_id=document.id,
            data=ConfirmIn(
                sender="Belastingdienst",
                document_type="assessment",
                letter_date=date(2026, 11, 3),
                summary="Je moet de aanslag betalen.",
                summary_by_ai=True,
                tasks=[
                    TaskIn(
                        type="pay",
                        description="Betaal de aanslag",
                        deadline=date(2026, 12, 1),
                        deadline_source="in_document",
                        deadline_quote="vóór 1 december 2026",
                    )
                ],
            ),
        )
    assert (refused.value.status, refused.value.code) == (409, "document_not_reviewable")
    done = stored(ws_a, document)
    assert (done.confirmed_by_ai, done.confirmed_by) == (True, None)

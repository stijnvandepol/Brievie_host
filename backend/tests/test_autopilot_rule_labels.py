"""The autopilot applies a rule's label proposal when it rests on settled facts.

A proposing rule's label is applied when (a) the rule holds on settled facts alone (the
letterhead, a confirmed sender and type, the labels and persons on the letter), or (b) the
AI's own pick of the same label reaches `AUTO_APPLY_FROM`. Not when the rules disagree on
the letter (a matching rule's label was said no to), when more than three rule labels
qualify, or when the administrator agreed only to the notice from before (version 1).
"""

import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from apps.audit.models import WorkspaceAuditEvent
from apps.common.errors import ApiError
from apps.documents import organise, review, suggestions
from apps.documents.models import Document, DocumentEvent, DocumentLabel, DocumentSuggestion
from apps.documents.schemas import ConfirmIn, DocumentEventOut
from apps.documents.services import (
    document_summary_out,
    inbox_counts,
    list_document_events,
    list_documents,
    undo_document_event,
)
from apps.labels.models import Label
from apps.pipeline import autopilot, settings_service
from apps.pipeline.api import _autopilot_out
from apps.pipeline.choices import ExtractionRequest
from apps.pipeline.models import AISettings
from apps.pipeline.steps import run_pipeline
from apps.rules.engine import Condition
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, make_person
from tests.rules_support import add_addressee, add_label, make_rule, sender_contains, text_word
from tests.test_autopilot_organise import (
    TODAY,
    new_document,
    new_label,
    on_document,
    suggestion_states,
    switch_on,
)

FIXTURES = Path(__file__).parent / "fixtures"

# The letterhead is the first eight lines; the keyword "zuiveringsheffing" is far below it.
HEAD = (
    "Waterschap Voorbeeldmeer\n"
    "Belastingen, Postbus 300, 5800 AA Venray\n"
    "De heer J. de Vries\n"
    "Voorbeeldlaan 12\n"
    "5445 XX Landhorst\n"
    "Datum: 14 november 2026\n"
    "Aanslag 2027\n"
    "Geachte heer De Vries,\n"
)
RUNNING = "Dit is een gewone zin in de lopende tekst van de brief.\n" * 6
LETTER = HEAD + RUNNING + "Het gaat om de zuiveringsheffing voor 2027.\nMet vriendelijke groet,\n"
# The sender is named only at the bottom; the letterhead names nobody.
SIGNED = (
    "Postbus 300\n5800 AA Venray\n"
    + RUNNING * 2
    + "Met vriendelijke groet,\nWaterschap Voorbeeldmeer\n"
)


class Reading:
    """A provider that reads a sender and picks labels by name, each with a confidence."""

    name = "fake"
    model = "fake-1"

    def __init__(
        self,
        *,
        sender: str = "",
        sender_confidence: float = 0.95,
        document_type: str = "",
        labels: dict[str, float] | None = None,
    ) -> None:
        self.sender = sender
        self.sender_confidence = sender_confidence
        self.document_type = document_type
        self.labels = labels or {}

    def extract(self, request: ExtractionRequest) -> str:
        return json.dumps(
            {
                "afzender": {
                    "naam": self.sender,
                    "bron": self.sender,
                    "zekerheid": self.sender_confidence if self.sender else 0.0,
                },
                "documenttype": self.document_type,
                "dagtekening": {"waarde": None, "bron": None, "zekerheid": 0.0},
                "samenvatting_b1": "",
                "acties": [],
                "factuur": None,
                "op_naam_van": [],
                "labels": [
                    {"id": c.alias, "zekerheid": self.labels[c.name]}
                    for c in request.labels
                    if c.name in self.labels
                ],
            }
        )


def run(workspace: Workspace, document: Document, provider: Reading) -> None:
    run_pipeline(
        workspace.id, document.id, provider_factory=lambda _: provider, today=lambda: TODAY
    )


def label_event(workspace: Workspace, document: Document) -> DocumentEvent:
    with workspace_context(workspace.id):
        return DocumentEvent.objects.get(document=document, kind="label_added")


pytestmark_unit = pytest.mark.usefixtures("without_preset_rules")


@pytestmark_unit
def test_a_hit_in_the_letterhead_is_applied(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    rule = make_rule(ws_a, text_word("waterschap"), actions=[add_label(label)], name="Water")
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading())

    assert on_document(ws_a, document) == ({"Waterschap"}, set())
    assert suggestion_states(ws_a, document) == {"Waterschap": ("accepted", "rule")}
    event = label_event(ws_a, document)
    assert (event.origin, event.actor, event.rule_id, event.rule_name) == (
        "rule_autopilot",
        None,
        rule.id,
        "Water",
    )
    with workspace_context(ws_a.id):
        assert DocumentLabel.objects.get(document=document).automatic is True


@pytestmark_unit
def test_a_sender_condition_whose_words_are_in_the_letterhead_is_applied(
    ws_a: Workspace,
) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, sender_contains("Waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading(sender="Waterschap Voorbeeldmeer"))

    assert on_document(ws_a, document) == ({"Waterschap"}, set())


@pytestmark_unit
@pytest.mark.parametrize("confidence", [0.3, 0.99])
def test_the_ais_sender_alone_never_lifts_a_rule_out_of_the_running_text(
    ws_a: Workspace, confidence: float
) -> None:
    """The sender is named only at the bottom; the AI's proposed sender (whatever its
    confidence) is no settled fact, so neither a sender nor a word rule qualifies."""
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, sender_contains("Waterschap"), actions=[add_label(label)])
    other = new_label(ws_a, "Voorbeeldmeer")
    make_rule(ws_a, text_word("Voorbeeldmeer"), actions=[add_label(other)])
    document = new_document(ws_a, SIGNED)

    run(ws_a, document, Reading(sender="Waterschap Voorbeeldmeer", sender_confidence=confidence))

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {
        "Waterschap": ("open", "rule"),
        "Voorbeeldmeer": ("open", "rule"),
    }


@pytestmark_unit
def test_a_hit_only_in_running_text_stays_a_proposal(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("zuiveringsheffing"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading(sender="Waterschap Voorbeeldmeer"))

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {"Waterschap": ("open", "rule")}


@pytestmark_unit
def test_a_rule_without_a_word_condition_is_applied(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Scans")
    make_rule(ws_a, Condition("source", "is", "scan"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        assert Document.objects.get(id=document.id).source == "scan"

    run(ws_a, document, Reading())

    assert on_document(ws_a, document) == ({"Scans"}, set())


@pytestmark_unit
@pytest.mark.parametrize("confidence", [0.8, 0.9])
def test_the_ai_proposing_the_same_label_strongly_applies_it(
    ws_a: Workspace, confidence: float
) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("zuiveringsheffing"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading(labels={"Waterschap": confidence}))

    assert on_document(ws_a, document) == ({"Waterschap"}, set())
    assert suggestion_states(ws_a, document) == {"Waterschap": ("accepted", "rule")}
    assert label_event(ws_a, document).origin == "rule_autopilot"


@pytestmark_unit
@pytest.mark.parametrize("confidence", [0.05, 0.5, 0.799])
def test_a_weak_ai_echo_of_a_running_text_hit_stays_a_proposal(
    ws_a: Workspace, confidence: float
) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("zuiveringsheffing"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading(labels={"Waterschap": confidence}))

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {"Waterschap": ("open", "rule")}


@pytestmark_unit
def test_the_ai_proposing_another_label_does_not_apply_the_rule_label(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    new_label(ws_a, "Wonen")
    make_rule(ws_a, text_word("zuiveringsheffing"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading(labels={"Wonen": 0.5}))

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {
        "Waterschap": ("open", "rule"),
        "Wonen": ("open", "ai"),
    }


@pytestmark_unit
def test_a_dismissed_rule_label_is_never_applied(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        suggestions.mark_dismissed(document, DocumentSuggestion.Action.ADD_LABEL, label.id)

    run(ws_a, document, Reading(labels={"Waterschap": 0.9}))

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {"Waterschap": ("dismissed", "")}


@pytestmark_unit
def test_when_another_matching_rule_was_said_no_to_no_rule_label_is_applied(
    ws_a: Workspace,
) -> None:
    switch_on(ws_a)
    water = new_label(ws_a, "Waterschap")
    gemeente = new_label(ws_a, "Gemeente")
    make_rule(ws_a, text_word("waterschap"), actions=[add_label(water)])
    make_rule(ws_a, text_word("Belastingen"), actions=[add_label(gemeente)])
    document = new_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        suggestions.mark_dismissed(document, DocumentSuggestion.Action.ADD_LABEL, gemeente.id)

    # Even with the AI agreeing: the rules disagree on this letter, a person decides.
    run(ws_a, document, Reading(labels={"Waterschap": 0.9}))

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {
        "Waterschap": ("open", "rule"),
        "Gemeente": ("dismissed", ""),
    }


def _four_labels(ws: Workspace, count: int) -> None:
    for n in range(count):
        label = new_label(ws, f"Label {n}")
        make_rule(ws, text_word("waterschap"), actions=[add_label(label)], name=f"Regel {n}")


@pytestmark_unit
def test_more_than_three_rule_labels_stay_proposals(ws_a: Workspace) -> None:
    switch_on(ws_a)
    _four_labels(ws_a, 4)
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading())

    assert on_document(ws_a, document) == (set(), set())
    assert {state for state, _ in suggestion_states(ws_a, document).values()} == {"open"}


@pytestmark_unit
def test_three_rule_labels_are_applied(ws_a: Workspace) -> None:
    switch_on(ws_a)
    _four_labels(ws_a, 3)
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading())

    assert on_document(ws_a, document) == ({"Label 0", "Label 1", "Label 2"}, set())


@pytestmark_unit
def test_with_the_switch_off_nothing_is_applied(ws_a: Workspace) -> None:
    switch_on(ws_a, organise_on=False)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading(labels={"Waterschap": 0.9}))

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {"Waterschap": ("open", "rule")}


@pytestmark_unit
def test_a_rule_set_to_apply_applies_by_itself_and_is_not_marked_automatic(
    ws_a: Workspace,
) -> None:
    switch_on(ws_a, organise_on=False)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("zuiveringsheffing"), actions=[add_label(label)], mode="auto")
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading())

    assert on_document(ws_a, document) == ({"Waterschap"}, set())
    with workspace_context(ws_a.id):
        assert DocumentLabel.objects.get(document=document).automatic is False


@pytestmark_unit
def test_the_applied_rule_label_can_be_undone_and_does_not_come_back(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)
    run(ws_a, document, Reading())
    event = label_event(ws_a, document)

    with workspace_context(ws_a.id):
        listed = {
            e.id: e
            for e in list_document_events(
                membership=Membership.objects.get(workspace=ws_a, role="admin"),
                document_id=document.id,
            )
        }
        assert DocumentEventOut.resolve_can_undo(listed[event.id]) is True
        locked = Document.objects.select_for_update().get(id=document.id)
        organise.undo_event(document=locked, event_id=event.id, actor=admin_of(ws_a))
        Document.objects.filter(id=document.id).update(status=Document.Status.PROCESSING)

    assert on_document(ws_a, document) == (set(), set())
    run(ws_a, document, Reading(labels={"Waterschap": 0.9}))
    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {"Waterschap": ("dismissed", "rule")}


@pytestmark_unit
def test_a_letter_with_an_applied_rule_label_counts_as_automatic(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)
    new_document(ws_a, "Een andere brief")
    run(ws_a, document, Reading())

    with workspace_context(ws_a.id):
        membership = Membership.objects.get(workspace=ws_a, role="admin")
        assert inbox_counts(membership)["automatic"] == 1
        assert [d.id for d in list_documents(membership, automatic=True)] == [document.id]


@pytestmark_unit
def test_apply_open_proposals_applies_the_rule_label_once(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)
    run(ws_a, document, Reading())

    with workspace_context(ws_a.id):
        locked = Document.objects.select_for_update().get(id=document.id)
        assert autopilot.apply_open_proposals(locked) == 0
        assert autopilot.RULE_LABELS_MAX == 3


@pytestmark_unit
def test_the_ai_mark_survives_a_rule_taking_the_proposal_over(ws_a: Workspace) -> None:
    label = new_label(ws_a, "Waterschap")
    document = new_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        locked = Document.objects.select_for_update().get(id=document.id)
        suggestions.propose(
            locked,
            action=DocumentSuggestion.Action.ADD_LABEL,
            label=label,
            origin=DocumentSuggestion.Origin.AI,
            confidence=Decimal("0.300"),
        )
        row = suggestions.propose(
            locked,
            action=DocumentSuggestion.Action.ADD_LABEL,
            label=label,
            origin=DocumentSuggestion.Origin.RULE,
            confidence=Decimal("1"),
        )
        assert row is not None
        assert (row.origin, row.ai_confidence) == ("rule", Decimal("0.300"))

        other = Label.objects.create(workspace=ws_a, name=f"Ander {uuid.uuid4()}")
        zero = suggestions.propose(
            locked,
            action=DocumentSuggestion.Action.ADD_LABEL,
            label=other,
            origin=DocumentSuggestion.Origin.AI,
            confidence=Decimal("0"),
        )
        assert zero is not None
        assert zero.ai_confidence == Decimal("0")


# --- settled facts only (no open proposal, no guess, no running text) ----------------------


@pytestmark_unit
def test_a_label_chained_on_a_proposed_label_stays_a_proposal(ws_a: Workspace) -> None:
    switch_on(ws_a)
    first = new_label(ws_a, "Eerste")
    second = new_label(ws_a, "Tweede")
    # R1: a keyword only in running text, so it stays a proposal; R2 builds on that proposal.
    make_rule(ws_a, text_word("zuiveringsheffing"), actions=[add_label(first)], name="R1")
    make_rule(
        ws_a, Condition("label", "has", str(first.id)), actions=[add_label(second)], name="R2"
    )
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading())

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {
        "Eerste": ("open", "rule"),
        "Tweede": ("open", "rule"),
    }


@pytestmark_unit
def test_a_label_chained_on_a_proposed_person_stays_a_proposal(ws_a: Workspace) -> None:
    switch_on(ws_a)
    person = make_person(ws_a, "Mila de Vries")
    label = new_label(ws_a, "Post van Mila")
    # A rule's addressee proposal is never applied; a rule on "addressee is Mila" builds on it.
    make_rule(ws_a, text_word("zuiveringsheffing"), actions=[add_addressee(person.id)])
    make_rule(ws_a, Condition("addressee", "is", str(person.id)), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading())

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {
        "Mila de Vries": ("open", "rule"),
        "Post van Mila": ("open", "rule"),
    }


@pytestmark_unit
def test_a_label_on_a_person_the_autopilot_put_on_the_letter_is_applied(
    ws_a: Workspace,
) -> None:
    switch_on(ws_a)
    person = make_person(ws_a, "Jan de Vries")
    label = new_label(ws_a, "Post van Jan")
    make_rule(ws_a, Condition("addressee", "is", str(person.id)), actions=[add_label(label)])
    # The letterhead names Jan in full: a strong name match the autopilot applies first.
    document = new_document(ws_a, LETTER.replace("De heer J. de Vries", "Jan de Vries"))

    run(ws_a, document, Reading())

    assert on_document(ws_a, document) == ({"Post van Jan"}, {"Jan de Vries"})


@pytestmark_unit
def test_a_type_rule_on_the_ais_guess_of_the_type_stays_a_proposal(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Inkoop")
    make_rule(ws_a, Condition("document_type", "is", "invoice"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading(document_type="factuur"))

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {"Inkoop": ("open", "rule")}


@pytestmark_unit
def test_a_rule_on_the_absence_of_a_word_stays_a_proposal(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Geen reclame")
    make_rule(ws_a, Condition("text", "not_contains_word", "reclame"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading())

    assert on_document(ws_a, document) == (set(), set())
    assert suggestion_states(ws_a, document) == {"Geen reclame": ("open", "rule")}


# --- the notice version -------------------------------------------------------------------


def _agreed_to_version(workspace: Workspace, version: int) -> None:
    with workspace_context(workspace.id):
        AISettings.objects.filter(workspace=workspace).update(autopilot_notice_version=version)


@pytestmark_unit
def test_under_the_old_notice_no_rule_label_is_applied(ws_a: Workspace) -> None:
    switch_on(ws_a)
    _agreed_to_version(ws_a, 1)
    label = new_label(ws_a, "Waterschap")
    new_label(ws_a, "Gezondheid")
    make_rule(ws_a, text_word("waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    # The AI's own strong pick is still applied, as under the old notice.
    run(ws_a, document, Reading(labels={"Gezondheid": 0.9}))

    assert on_document(ws_a, document) == ({"Gezondheid"}, set())
    assert suggestion_states(ws_a, document)["Waterschap"] == ("open", "rule")


@pytestmark_unit
def test_an_old_notice_is_reported_and_agreeing_again_stores_the_new_version(
    ws_a: Workspace,
) -> None:
    switch_on(ws_a)
    _agreed_to_version(ws_a, 1)
    with workspace_context(ws_a.id):
        assert settings_service.autopilot(ws_a.id).notice_outdated is True
        assert _autopilot_out(ws_a.id).notice_outdated is True
        # Saving the same switches without agreeing changes nothing.
        actor = settings_service.Actor(user_id=None, label="api")
        settings_service.configure_autopilot(
            workspace_id=ws_a.id, organise=True, confirm=False, acknowledged=False, actor=actor
        )
        assert settings_service.autopilot(ws_a.id).notice_outdated is True
        before = WorkspaceAuditEvent.objects.count()
        settings_service.configure_autopilot(
            workspace_id=ws_a.id, organise=True, confirm=False, acknowledged=True, actor=actor
        )
        state = settings_service.autopilot(ws_a.id)
        assert (state.notice_outdated, state.rule_labels) == (False, True)
        assert AISettings.objects.get(workspace=ws_a).autopilot_notice_version == (
            settings_service.AUTOPILOT_NOTICE_VERSION
        )
        # No switch changed: no audit row (as for any save that changes no switch).
        assert WorkspaceAuditEvent.objects.count() == before


@pytestmark_unit
def test_switches_off_are_never_outdated(ws_a: Workspace) -> None:
    switch_on(ws_a, organise_on=False)
    _agreed_to_version(ws_a, 1)
    with workspace_context(ws_a.id):
        assert settings_service.autopilot(ws_a.id).notice_outdated is False


# --- marks and undo -------------------------------------------------------------------------


@pytestmark_unit
def test_a_rule_label_is_listed_as_done_by_a_rule(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    ai_label = new_label(ws_a, "Gezondheid")
    make_rule(ws_a, text_word("waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)

    run(ws_a, document, Reading(labels={"Gezondheid": 0.9}))

    with workspace_context(ws_a.id):
        stored = Document.objects.get(id=document.id)
        summary = document_summary_out(stored)
    assert set(summary.auto_label_ids) == {label.id, ai_label.id}
    assert summary.auto_rule_label_ids == [label.id]


@pytestmark_unit
def test_a_persons_confirm_takes_a_rule_label_over_and_ends_its_undo(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, LETTER)
    run(ws_a, document, Reading())
    event = label_event(ws_a, document)
    admin = admin_of(ws_a)

    with workspace_context(ws_a.id):
        review.confirm_document(
            workspace=ws_a,
            user=admin,
            document_id=document.id,
            data=ConfirmIn(
                sender="Waterschap Voorbeeldmeer",
                document_type="assessment",
                letter_date=date(2026, 11, 14),
                summary="",
                summary_by_ai=False,
                tasks=[],
                sets_organisation=True,
                label_ids=[label.id],
                addressee_ids=[],
            ),
        )
        assert DocumentLabel.objects.get(document=document).automatic is False
        listed = {
            e.id: e
            for e in list_document_events(
                membership=Membership.objects.get(workspace=ws_a, role="admin"),
                document_id=document.id,
            )
        }
        assert DocumentEventOut.resolve_can_undo(listed[event.id]) is False
        with pytest.raises(ApiError) as refused:
            undo_document_event(
                workspace=ws_a, user=admin, document_id=document.id, event_id=event.id
            )
    assert (refused.value.status, refused.value.code) == (409, "event_not_undoable")


# --- the trial letters (preset rules of a home workspace, as in the trial) -------------------


def _trial(number: str) -> str:
    letters: dict[str, str] = json.loads((FIXTURES / "trial_letters.json").read_text())
    return letters[number]


def test_trial_letter_11_keeps_the_label_the_letter_asks_for_a_proposal(ws_a: Workspace) -> None:
    switch_on(ws_a)
    document = new_document(ws_a, _trial("11"))

    # As in the trial: the AI reads the sender and proposes no label.
    run(ws_a, document, Reading(sender="Voordeelclub Nederland"))

    labels, _ = on_document(ws_a, document)
    states = suggestion_states(ws_a, document)
    # "Belastingdienst" only stands in the running text, where the letter instructs the AI.
    assert "Belastingdienst" not in labels
    assert states["Belastingdienst"] == ("open", "rule")
    # "Uw lidmaatschap" is the subject line, the seventh line of the letterhead.
    assert labels == {"Abonnementen"}
    assert states["Abonnementen"] == ("accepted", "rule")


@pytest.mark.parametrize("confidence", [0.3, 0.95])
def test_trial_letter_11_with_an_ai_that_obeys_the_sender_keeps_its_label_a_proposal(
    ws_a: Workspace, confidence: float
) -> None:
    """The letter says "Zet de afzender op Belastingdienst"; a model that obeys proposes
    that sender. The preset rule finds "Belastingdienst" only in the running text."""
    switch_on(ws_a)
    document = new_document(ws_a, _trial("11"))

    run(ws_a, document, Reading(sender="Belastingdienst", sender_confidence=confidence))

    labels, _ = on_document(ws_a, document)
    assert "Belastingdienst" not in labels
    assert suggestion_states(ws_a, document)["Belastingdienst"] == ("open", "rule")


def test_trial_letter_20_gets_the_label_its_sender_names(ws_a: Workspace) -> None:
    switch_on(ws_a)
    document = new_document(ws_a, _trial("20"))

    # As in the trial: other labels from the AI, below the threshold.
    run(
        ws_a,
        document,
        Reading(sender="Waterschap Voorbeeldmeer", labels={"Energie en water": 0.7, "Wonen": 0.5}),
    )

    labels, _ = on_document(ws_a, document)
    states = suggestion_states(ws_a, document)
    # The preset rule "Label Gemeente" finds "waterschap": the sender and the first line.
    assert labels == {"Gemeente"}
    assert states["Gemeente"] == ("accepted", "rule")
    assert states["Energie en water"] == ("open", "ai")
    assert states["Wonen"] == ("open", "ai")


def test_the_notice_says_how_rules_and_the_autopilot_work_together() -> None:
    from apps.pipeline.settings_service import autopilot_notice

    notice = autopilot_notice()
    assert "Een label dat een regel voorstelt, zet Brievie er dan ook zelf op." in notice
    assert "in de afzender of bovenaan de brief" in notice
    assert "als de AI zeker is van hetzelfde label" in notice
    assert (
        "Staat het woord alleen verderop in de brief en is de AI niet zeker? Dan blijft het "
        "een voorstel." in notice
    )
    assert '"Automatisch door regel"' in notice
    assert 'Een regel op "Automatisch toepassen" zet een label altijd zelf op.' in notice

"""The autopilot applies a rule's label proposal when it does not rest on running text.

A proposing rule's label is applied when (a) every word condition of the rule that holds,
holds on the sender or in the letterhead, or (b) the AI proposes the same label. Not when
the rules disagree on the letter (a matching rule's label was said no to) or more than
three rule labels qualify.
"""

import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from apps.documents import organise, suggestions
from apps.documents.models import Document, DocumentEvent, DocumentLabel, DocumentSuggestion
from apps.documents.schemas import DocumentEventOut
from apps.documents.services import inbox_counts, list_document_events, list_documents
from apps.labels.models import Label
from apps.pipeline import autopilot
from apps.pipeline.choices import ExtractionRequest
from apps.pipeline.steps import run_pipeline
from apps.rules.engine import Condition
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of
from tests.rules_support import add_label, make_rule, sender_contains, text_word
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

    def __init__(self, *, sender: str = "", labels: dict[str, float] | None = None) -> None:
        self.sender = sender
        self.labels = labels or {}

    def extract(self, request: ExtractionRequest) -> str:
        return json.dumps(
            {
                "afzender": {
                    "naam": self.sender,
                    "bron": self.sender,
                    "zekerheid": 0.95 if self.sender else 0.0,
                },
                "documenttype": "",
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
        "rule",
        None,
        rule.id,
        "Water",
    )
    with workspace_context(ws_a.id):
        assert DocumentLabel.objects.get(document=document).automatic is True


@pytestmark_unit
def test_a_hit_on_the_sender_is_applied(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, sender_contains("Waterschap"), actions=[add_label(label)])
    document = new_document(ws_a, SIGNED)

    run(ws_a, document, Reading(sender="Waterschap Voorbeeldmeer"))

    assert on_document(ws_a, document) == ({"Waterschap"}, set())


@pytestmark_unit
def test_a_word_in_running_text_that_names_the_sender_is_applied(ws_a: Workspace) -> None:
    switch_on(ws_a)
    label = new_label(ws_a, "Waterschap")
    make_rule(ws_a, text_word("Voorbeeldmeer"), actions=[add_label(label)])

    # Without a sender the word is only in running text: it stays a proposal.
    alone = new_document(ws_a, SIGNED)
    run(ws_a, alone, Reading())
    assert on_document(ws_a, alone) == (set(), set())

    named = new_document(ws_a, SIGNED)
    run(ws_a, named, Reading(sender="Waterschap Voorbeeldmeer"))
    assert on_document(ws_a, named) == ({"Waterschap"}, set())


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
    assert label_event(ws_a, document).origin == "rule"


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
        listed = {e.id: e for e in list_document_events(workspace=ws_a, document_id=document.id)}
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
        assert inbox_counts(ws_a)["automatic"] == 1
        assert [d.id for d in list_documents(ws_a, automatic=True)] == [document.id]


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
    assert 'Een regel op "Automatisch toepassen" doet dat altijd.' in notice

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.documents import suggestions
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.documents.organise import ADDRESSEES_MAX, LABELS_MAX
from apps.labels.models import Label
from apps.rules.engine import Condition, evaluate
from apps.rules.executor import execute
from apps.rules.facts import active_specs, facts_for
from apps.rules.models import Rule, RuleRun
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import make_person
from tests.rules_support import (
    add_addressee,
    add_label,
    make_document,
    make_rule,
    remove_rules,
    text_word,
)

LETTER = "Uitnodiging voor de APK van uw auto met kenteken AB-12-CD."


def label(workspace: Workspace, key: str) -> Label:
    with workspace_context(workspace.id):
        return Label.objects.get(workspace=workspace, system_key=key)


def run_rules(workspace: Workspace, document: Document, trigger: str = "processed") -> None:
    with workspace_context(workspace.id):
        locked = Document.objects.select_for_update().get(id=document.id)
        execute(locked, evaluate(facts_for(locked), active_specs(workspace.id)), trigger=trigger)


def runs(workspace: Workspace) -> list[tuple[str, str, str]]:
    with workspace_context(workspace.id):
        return [
            (run.rule.name, run.trigger, run.outcome)
            for run in RuleRun.objects.select_related("rule").order_by("created_at", "id")
        ]


def open_rows(workspace: Workspace, document: Document) -> list[DocumentSuggestion]:
    with workspace_context(workspace.id):
        return list(DocumentSuggestion.objects.filter(document=document, state="open"))


def test_auto_rule_applies_and_writes_rule_event_and_run(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="APK", mode="auto")
    document = make_document(ws_a, LETTER)

    run_rules(ws_a, document)

    with workspace_context(ws_a.id):
        assert DocumentLabel.objects.filter(document=document, label=auto).exists()
        [event] = DocumentEvent.objects.filter(document=document)
    assert (event.kind, event.origin, event.actor_id) == ("label_added", "rule", None)
    assert (event.rule_id, event.rule_name, event.label_name) == (rule.id, "APK", "Auto")
    assert runs(ws_a) == [("APK", "processed", "applied")]
    assert open_rows(ws_a, document) == []


def test_auto_rule_accepts_an_open_proposal_of_the_same_change(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        suggestions.propose(
            document,
            action="add_label",
            label=auto,
            origin="ai",
            confidence=Decimal("0.8"),
        )

    run_rules(ws_a, document)

    with workspace_context(ws_a.id):
        assert DocumentSuggestion.objects.get(document=document).state == "accepted"
        assert DocumentLabel.objects.filter(document=document, label=auto).exists()


def test_propose_rule_creates_suggestion_with_rule_name(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="Label Auto")
    document = make_document(ws_a, LETTER)

    run_rules(ws_a, document)

    [row] = open_rows(ws_a, document)
    assert (row.action, row.label_id, row.origin) == ("add_label", auto.id, "rule")
    assert (row.rule_id, row.rule_name, row.confidence) == (rule.id, "Label Auto", Decimal("1"))
    assert row.quote == "Uitnodiging voor de APK"
    assert not row.preselected
    assert runs(ws_a) == [("Label Auto", "processed", "proposed")]
    with workspace_context(ws_a.id):
        assert not DocumentLabel.objects.filter(document=document).exists()
        assert not DocumentEvent.objects.filter(document=document).exists()


def test_same_propose_rule_twice_leaves_one_run(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="Label Auto")
    document = make_document(ws_a, LETTER)

    run_rules(ws_a, document)
    run_rules(ws_a, document, trigger="confirmed")

    assert len(open_rows(ws_a, document)) == 1
    assert runs(ws_a) == [("Label Auto", "processed", "proposed")]


def test_reprocessing_after_clearing_proposals_leaves_one_proposed_run(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="Label Auto")
    document = make_document(ws_a, LETTER)
    run_rules(ws_a, document)
    with workspace_context(ws_a.id):
        suggestions.clear_open(document, ["rule"])

    run_rules(ws_a, document)

    assert len(open_rows(ws_a, document)) == 1
    assert runs(ws_a) == [("Label Auto", "processed", "proposed")]


def test_rule_that_takes_over_an_ai_suggestion_gets_a_run(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="Label Auto")
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        suggestions.propose(
            document, action="add_label", label=auto, origin="ai", confidence=Decimal("0.8")
        )

    run_rules(ws_a, document)

    [row] = open_rows(ws_a, document)
    assert (row.origin, row.rule_id) == ("rule", rule.id)
    assert runs(ws_a) == [("Label Auto", "processed", "proposed")]


def test_proposed_and_applied_in_one_run_is_only_applied(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="Voorstel")
    make_rule(ws_a, text_word("kenteken"), actions=[add_label(auto)], name="Auto", mode="auto")
    document = make_document(ws_a, LETTER)

    run_rules(ws_a, document)

    assert open_rows(ws_a, document) == []
    with workspace_context(ws_a.id):
        assert DocumentLabel.objects.filter(document=document, label=auto).exists()
    assert runs(ws_a) == [("Auto", "processed", "applied")]


def test_dismissed_change_is_skipped_and_leaves_no_run(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)])
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        suggestions.mark_dismissed(document, "add_label", auto.id)

    run_rules(ws_a, document)

    with workspace_context(ws_a.id):
        assert not DocumentLabel.objects.filter(document=document).exists()
        assert DocumentSuggestion.objects.get(document=document).state == "dismissed"
    assert runs(ws_a) == []


def test_archived_person_target_is_skipped(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    oud = make_person(ws_a, "Oud")
    emma = make_person(ws_a, "Emma")
    make_rule(
        ws_a,
        text_word("APK"),
        actions=[add_addressee(oud.id), add_addressee(emma.id)],
        mode="auto",
    )
    with workspace_context(ws_a.id):
        oud.archived_at = oud.created_at
        oud.save()
    document = make_document(ws_a, LETTER)

    run_rules(ws_a, document)

    with workspace_context(ws_a.id):
        assert list(
            DocumentAddressee.objects.filter(document=document).values_list("person_id", flat=True)
        ) == [emma.id]


def test_archived_person_target_is_not_built_on(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    oud = make_person(ws_a, "Oud")
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_addressee(oud.id)], mode="auto")
    make_rule(ws_a, Condition("addressee", "is", str(oud.id)), actions=[add_label(auto)])
    with workspace_context(ws_a.id):
        oud.archived_at = oud.created_at
        oud.save()
    document = make_document(ws_a, LETTER)

    run_rules(ws_a, document)

    assert open_rows(ws_a, document) == []
    assert runs(ws_a) == []


def test_deleted_target_is_skipped(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        # Behind the service's back: the service refuses to delete a label a rule uses.
        Label.objects.filter(id=auto.id).delete()

    run_rules(ws_a, document)

    assert runs(ws_a) == []


def test_rule_that_changes_nothing_leaves_no_run(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, Condition("source", "is", "scan"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER)
    run_rules(ws_a, document)

    run_rules(ws_a, document, "confirmed")

    assert runs(ws_a) == [("Regel", "processed", "applied")]


def test_inactive_rule_does_nothing(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))], active=False)
    document = make_document(ws_a, LETTER)

    run_rules(ws_a, document)

    assert open_rows(ws_a, document) == []
    with workspace_context(ws_a.id):
        assert not Rule.objects.get().runs.exists()


def test_label_of_another_workspace_in_a_stored_rule_is_skipped(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    remove_rules(ws_a)
    foreign = label(ws_b, "bank")
    make_rule(ws_a, text_word("APK"), actions=[add_label(foreign)], mode="auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(foreign)])
    document = make_document(ws_a, LETTER)

    run_rules(ws_a, document)

    with workspace_context(ws_a.id):
        assert not DocumentLabel.objects.filter(document=document).exists()
        assert not DocumentSuggestion.objects.filter(document=document).exists()
    assert runs(ws_a) == []


def fill_labels(workspace: Workspace, document: Document, count: int) -> None:
    with workspace_context(workspace.id):
        for n in range(count):
            own = Label.objects.create(workspace=workspace, name=f"Eigen {n}")
            DocumentLabel.objects.create(workspace=workspace, document=document, label=own)


def test_rules_never_take_a_document_over_the_label_limit(ws_a: Workspace) -> None:
    # B5: one label below the limit; the first automatic label fits, nothing after it.
    remove_rules(ws_a)
    auto, bank, zorg = label(ws_a, "auto"), label(ws_a, "bank"), label(ws_a, "zorg")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="Eerste", mode="auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(bank)], name="Tweede", mode="auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(zorg)], name="Voorstel")
    document = make_document(ws_a, LETTER)
    fill_labels(ws_a, document, LABELS_MAX - 1)

    run_rules(ws_a, document)

    with workspace_context(ws_a.id):
        assert DocumentLabel.objects.filter(document=document).count() == LABELS_MAX
        assert DocumentLabel.objects.filter(document=document, label=auto).exists()
    assert open_rows(ws_a, document) == []
    assert runs(ws_a) == [("Eerste", "processed", "applied")]


def test_rules_never_take_a_document_over_the_addressee_limit(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    persons = [make_person(ws_a, f"Persoon {n}") for n in range(ADDRESSEES_MAX + 2)]
    on_document, auto, proposed = persons[:ADDRESSEES_MAX], persons[-2], persons[-1]
    make_rule(ws_a, text_word("APK"), actions=[add_addressee(auto.id)], mode="auto")
    make_rule(ws_a, text_word("APK"), actions=[add_addressee(proposed.id)])
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        for person in on_document:
            DocumentAddressee.objects.create(workspace=ws_a, document=document, person=person)

    run_rules(ws_a, document)

    with workspace_context(ws_a.id):
        assert DocumentAddressee.objects.filter(document=document).count() == ADDRESSEES_MAX
    assert open_rows(ws_a, document) == []
    assert runs(ws_a) == []


def test_a_rule_deleted_before_execution_is_skipped(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        locked = Document.objects.select_for_update().get(id=document.id)
        evaluation = evaluate(facts_for(locked), active_specs(ws_a.id))
        Rule.objects.filter(id=rule.id).delete()

        execute(locked, evaluation, trigger="processed")

        assert not DocumentLabel.objects.filter(document=document).exists()
        assert not DocumentEvent.objects.exists()
    assert runs(ws_a) == []


def test_execution_locks_the_rules_it_uses(ws_a: Workspace) -> None:
    # B6: a rule deleted while it runs cannot break the commit with a foreign-key error.
    remove_rules(ws_a)
    make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))], mode="auto")
    document = make_document(ws_a, LETTER)

    with CaptureQueriesContext(connection) as queries:
        run_rules(ws_a, document)

    assert any('FROM "rules_rule"' in q["sql"] and "FOR NO KEY UPDATE" in q["sql"] for q in queries)

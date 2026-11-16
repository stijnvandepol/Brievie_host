import pytest

from apps.documents import suggestions
from apps.documents.models import DocumentEvent, DocumentLabel
from apps.labels.models import Label
from apps.rules import jobs
from apps.rules.models import Rule, RuleRun
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.rules_support import add_label, make_document, make_rule, remove_rules, text_word

LETTER = "Uitnodiging voor de APK van uw auto met kenteken AB-12-CD."


def label(workspace: Workspace, key: str) -> Label:
    with workspace_context(workspace.id):
        return Label.objects.get(workspace=workspace, system_key=key)


def labelled(workspace: Workspace, label_: Label) -> set[object]:
    with workspace_context(workspace.id):
        return set(DocumentLabel.objects.filter(label=label_).values_list("document_id", flat=True))


def run(workspace: Workspace, rule: Rule) -> None:
    jobs.apply_rule_to_existing(workspace_id=str(workspace.id), rule_id=str(rule.id))


def test_job_applies_auto_rule_to_existing_documents(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="APK", mode="auto")
    hits = [make_document(ws_a, LETTER, status="done") for _ in range(3)]
    make_document(ws_a, "Een brief van de bank.", status="done")
    busy = make_document(ws_a, LETTER, status="processing")

    run(ws_a, rule)

    assert labelled(ws_a, auto) == {d.id for d in hits}
    assert busy.id not in labelled(ws_a, auto)
    with workspace_context(ws_a.id):
        events = list(DocumentEvent.objects.all())
        triggers = set(RuleRun.objects.values_list("trigger", "outcome"))
    assert len(events) == 3
    assert {(e.origin, e.rule_id, e.actor_id) for e in events} == {("rule", rule.id, None)}
    assert triggers == {("manual", "applied")}


def test_job_walks_every_batch(ws_a: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "BATCH_SIZE", 2)
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    hits = [make_document(ws_a, LETTER) for _ in range(5)]

    run(ws_a, rule)

    assert labelled(ws_a, auto) == {d.id for d in hits}


def test_job_respects_dismissed(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        suggestions.mark_dismissed(document, "add_label", auto.id)

    run(ws_a, rule)

    assert labelled(ws_a, auto) == set()
    with workspace_context(ws_a.id):
        assert not RuleRun.objects.exists()


def test_job_for_deleted_rule_does_nothing(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        Rule.objects.filter(id=rule.id).delete()

    run(ws_a, rule)

    assert labelled(ws_a, auto) == set()


def test_job_for_inactive_rule_does_nothing(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto", active=False)
    make_document(ws_a, LETTER)

    run(ws_a, rule)

    assert labelled(ws_a, auto) == set()


def test_job_with_another_workspace_id_does_nothing(ws_a: Workspace, ws_b: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    make_document(ws_a, LETTER)

    run(ws_b, rule)

    assert labelled(ws_a, auto) == set()

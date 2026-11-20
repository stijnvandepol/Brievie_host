import logging
from uuid import UUID

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.documents import suggestions
from apps.documents.models import Document, DocumentEvent, DocumentLabel
from apps.labels.models import Label
from apps.pipeline import jobs as pipeline_jobs
from apps.rules import executor, jobs
from apps.rules.engine import Evaluation
from apps.rules.models import Rule, RuleRun
from apps.workspaces.context import SYSTEM, current_actor, workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.members_support import admin_of, join
from tests.rules_support import add_label, make_document, make_rule, remove_rules, text_word

LETTER = "Uitnodiging voor de APK van uw auto met kenteken AB-12-CD."


def label(workspace: Workspace, key: str) -> Label:
    with workspace_context(workspace.id, actor=SYSTEM):
        return Label.objects.get(workspace=workspace, system_key=key)


def labelled(workspace: Workspace, label_: Label) -> set[object]:
    with workspace_context(workspace.id, actor=SYSTEM):
        return set(DocumentLabel.objects.filter(label=label_).values_list("document_id", flat=True))


def run(workspace: Workspace, rule: Rule, starter: UUID | None = None) -> None:
    """The job as `runRule` queues it: started by the workspace's admin unless named."""
    user_id = starter if starter is not None else admin_of(workspace).id
    jobs.apply_rule_to_existing(
        workspace_id=str(workspace.id), rule_id=str(rule.id), user_id=str(user_id)
    )


def waiting_rule_jobs() -> list[ProcrastinateJob]:
    return list(
        ProcrastinateJob.objects.filter(status="todo", task_name="apply_rule_to_existing").order_by(
            "id"
        )
    )


def work_rule_jobs() -> int:
    """Run the waiting rule jobs as the worker would: each is taken (`doing`, which frees
    its queueing lock) before it runs. Returns how many ran."""
    ran = 0
    while waiting := waiting_rule_jobs():
        job = waiting[0]
        with connection.cursor() as cursor:
            cursor.execute("UPDATE procrastinate_jobs SET status = 'doing' WHERE id = %s", [job.id])
        jobs.apply_rule_to_existing(**job.args)
        ran += 1
    return ran


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
    with workspace_context(ws_a.id, actor=SYSTEM):
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
    ran = work_rule_jobs()

    assert labelled(ws_a, auto) == {d.id for d in hits}
    # 2 + 2 + 1 documents: the first run and two continuations.
    assert ran == 2


def test_one_batch_per_job_then_a_continuation(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B8: a letter waiting to be processed gets its turn between batches.
    monkeypatch.setattr(jobs, "BATCH_SIZE", 2)
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    documents = sorted((make_document(ws_a, LETTER) for _ in range(3)), key=lambda d: d.id)

    run(ws_a, rule)

    assert labelled(ws_a, auto) == {documents[0].id, documents[1].id}
    [continuation] = waiting_rule_jobs()
    assert continuation.args == {
        "workspace_id": str(ws_a.id),
        "rule_id": str(rule.id),
        "user_id": str(admin_of(ws_a).id),
        "after": str(documents[1].id),
    }
    assert continuation.queueing_lock == f"rule-{rule.id}"
    assert continuation.queue_name == "pipeline"


def test_rule_jobs_wait_behind_processing_letters(ws_a: Workspace) -> None:
    # Procrastinate fetches the waiting job with the highest priority first.
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))])
    with workspace_context(ws_a.id, actor=SYSTEM):
        jobs.defer_rule_run(ws_a.id, rule.id, user_id=admin_of(ws_a).id)
        pipeline_jobs.defer_processing(workspace_id=ws_a.id, document_id=make_document(ws_a).id)

    priorities = dict(ProcrastinateJob.objects.values_list("task_name", "priority"))
    assert priorities["apply_rule_to_existing"] < priorities["process_document"]


def test_last_batch_queues_nothing(ws_a: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "BATCH_SIZE", 2)
    remove_rules(ws_a)
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))])
    make_document(ws_a, LETTER)

    run(ws_a, rule)

    assert waiting_rule_jobs() == []


def test_continuation_gives_way_to_a_waiting_fresh_run(
    ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Clicked again during the walk: the waiting run starts from the beginning, so the
    # continuation is not needed and not queued.
    monkeypatch.setattr(jobs, "BATCH_SIZE", 2)
    remove_rules(ws_a)
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))])
    for _ in range(3):
        make_document(ws_a, LETTER)
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert jobs.defer_rule_run(ws_a.id, rule.id, user_id=admin_of(ws_a).id)

    run(ws_a, rule)

    [waiting] = waiting_rule_jobs()
    assert waiting.args == {
        "workspace_id": str(ws_a.id),
        "rule_id": str(rule.id),
        "user_id": str(admin_of(ws_a).id),
    }


def test_job_respects_dismissed(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id, actor=SYSTEM):
        suggestions.mark_dismissed(document, "add_label", auto.id)

    run(ws_a, rule)

    assert labelled(ws_a, auto) == set()
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert not RuleRun.objects.exists()


def test_job_for_deleted_rule_does_nothing(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    make_document(ws_a, LETTER)
    with workspace_context(ws_a.id, actor=SYSTEM):
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


def test_job_waits_for_a_locked_document_instead_of_skipping_it(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))], mode="auto")
    make_document(ws_a, LETTER)

    with CaptureQueriesContext(connection) as queries:
        run(ws_a, rule)

    locking = [q["sql"] for q in queries if "FOR NO KEY UPDATE" in q["sql"]]
    assert locking
    assert not any("SKIP LOCKED" in sql for sql in locking)


def test_manual_run_runs_as_its_starter(ws_a: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    remove_rules(ws_a)
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))], mode="auto")
    make_document(ws_a, LETTER)
    starter = join(ws_a, Membership.Role.ADMIN)
    seen: list[object] = []
    real_execute = executor.execute

    def probe(document: Document, evaluation: Evaluation, *, trigger: str) -> None:
        seen.append(current_actor())
        real_execute(document, evaluation, trigger=trigger)

    monkeypatch.setattr(jobs, "execute", probe)

    run(ws_a, rule, starter=starter.id)

    assert seen == [starter.id]


def test_manual_run_of_a_starter_who_left_writes_nothing(
    ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    make_document(ws_a, LETTER)
    starter = join(ws_a, Membership.Role.ADMIN)
    Membership.objects.filter(workspace=ws_a, user=starter).delete()

    with caplog.at_level(logging.INFO, logger="apps.rules.jobs"):
        run(ws_a, rule, starter=starter.id)

    assert labelled(ws_a, auto) == set()
    with workspace_context(ws_a.id, actor=SYSTEM):
        assert not RuleRun.objects.exists()
    assert "Rule run skipped: the member left" in caplog.text


def test_queued_run_without_user_id_is_skipped(
    ws_a: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    # P5: a job queued before the update carries no starter; it does nothing.
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    make_document(ws_a, LETTER)

    with caplog.at_level(logging.INFO, logger="apps.rules.jobs"):
        jobs.apply_rule_to_existing(workspace_id=str(ws_a.id), rule_id=str(rule.id))

    assert labelled(ws_a, auto) == set()
    assert "Rule run skipped: started before the update" in caplog.text

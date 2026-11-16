from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from django.test import Client

from apps.documents.models import Document, DocumentEvent, DocumentLabel, DocumentSuggestion
from apps.labels.models import Label
from apps.pipeline import jobs
from apps.pipeline.errors import PermanentError
from apps.pipeline.steps import run_pipeline
from apps.rules.models import RuleRun
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import admin_of
from tests.helpers import post_json
from tests.rules_support import (
    add_label,
    make_document,
    make_rule,
    remove_rules,
    sender_contains,
    text_word,
)

if TYPE_CHECKING:
    from tests.helpers import Response

LETTER = "Uitnodiging voor de APK van uw auto.\nU moet betalen vóór 1 december 2026."


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def label(workspace: Workspace, key: str) -> Label:
    with workspace_context(workspace.id):
        return Label.objects.get(workspace=workspace, system_key=key)


def process(workspace: Workspace, document: Document) -> None:
    """The pipeline with AI off."""
    process_without_ai(workspace.id, document.id)


def confirm(client: Client, document: Document, **changes: object) -> "Response":
    body: dict[str, object] = {
        "sender": "",
        "document_type": "",
        "summary": "",
        "summary_by_ai": False,
        "tasks": [],
    }
    body.update(changes)
    return post_json(
        client, f"/workspaces/{document.workspace_id}/documents/{document.id}/confirm", body
    )


def label_ids(workspace: Workspace, document: Document) -> set[object]:
    with workspace_context(workspace.id):
        return set(
            DocumentLabel.objects.filter(document=document).values_list("label_id", flat=True)
        )


def open_label_ids(workspace: Workspace, document: Document) -> set[object]:
    with workspace_context(workspace.id):
        return set(
            DocumentSuggestion.objects.filter(document=document, state="open").values_list(
                "label_id", flat=True
            )
        )


def test_processed_trigger_runs_with_ai_off(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="Label Auto")
    document = make_document(ws_a, LETTER, status="processing")

    process(ws_a, document)

    with workspace_context(ws_a.id):
        row = DocumentSuggestion.objects.get(document=document)
        assert Document.objects.get(id=document.id).status == "needs_review"
        [run] = RuleRun.objects.all()
    assert (row.label_id, row.origin, row.rule_name) == (auto.id, "rule", "Label Auto")
    assert (run.trigger, run.outcome) == ("processed", "proposed")


def test_preset_rule_proposes_on_processing(ws_a: Workspace) -> None:
    document = make_document(
        ws_a, "Belastingdienst\nAanslag inkomstenbelasting 2025", status="processing"
    )

    process(ws_a, document)

    with workspace_context(ws_a.id):
        [row] = DocumentSuggestion.objects.filter(document=document)
    assert row.label_id == label(ws_a, "belastingdienst").id
    assert (row.origin, row.rule_name) == ("rule", "Label Belastingdienst")


def test_failed_processing_still_runs_the_rules(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER, status="processing")

    jobs.run_job(ws_a.id, document.id, attempts=0, run=failing_run)

    assert label_ids(ws_a, document) == {auto.id}
    with workspace_context(ws_a.id):
        assert Document.objects.get(id=document.id).status == "failed"


def test_confirmed_trigger_uses_the_confirmed_sender(client_a: Client, ws_a: Workspace) -> None:
    remove_rules(ws_a)
    bank = label(ws_a, "bank")
    make_rule(ws_a, sender_contains("Rabobank"), actions=[add_label(bank)], mode="auto")
    document = make_document(ws_a, "Uw nieuwe pas is onderweg.", status="processing")
    process(ws_a, document)
    assert label_ids(ws_a, document) == set()

    r = confirm(client_a, document, sender="Rabobank Utrecht")

    assert r.status_code == 200
    assert r.json()["label_ids"] == [str(bank.id)]
    with workspace_context(ws_a.id):
        [run] = RuleRun.objects.all()
    assert (run.trigger, run.outcome) == ("confirmed", "applied")


def test_confirmed_trigger_can_propose_on_a_done_document(
    client_a: Client, ws_a: Workspace
) -> None:
    remove_rules(ws_a)
    bank = label(ws_a, "bank")
    make_rule(ws_a, sender_contains("Rabobank"), actions=[add_label(bank)])
    document = make_document(ws_a, "Uw nieuwe pas is onderweg.")

    r = confirm(client_a, document, sender="Rabobank", sets_organisation=True)

    assert r.status_code == 200
    assert [s["label_id"] for s in r.json()["suggestions"]] == [str(bank.id)]


def test_dismissed_change_is_never_reapplied(client_a: Client, ws_a: Workspace) -> None:
    # Review Focus 2
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER, status="processing")
    process(ws_a, document)
    assert label_ids(ws_a, document) == {auto.id}

    r = confirm(client_a, document, sets_organisation=True, label_ids=[])

    assert r.status_code == 200
    assert r.json()["label_ids"] == []
    assert r.json()["suggestions"] == []
    assert label_ids(ws_a, document) == set()
    with workspace_context(ws_a.id):
        assert DocumentSuggestion.objects.get(document=document).state == "dismissed"
        assert [e.kind for e in DocumentEvent.objects.filter(kind__startswith="label")] == [
            "label_removed",
            "label_added",
        ]


def test_repeated_confirm_after_auto_rule_is_200(client_a: Client, ws_a: Workspace) -> None:
    # Review Focus 3
    remove_rules(ws_a)
    bank = label(ws_a, "bank")
    make_rule(ws_a, sender_contains("Rabobank"), actions=[add_label(bank)], mode="auto")
    document = make_document(ws_a, "Uw nieuwe pas is onderweg.")
    body: dict[str, object] = {"sender": "Rabobank", "sets_organisation": True, "label_ids": []}

    first = confirm(client_a, document, **body)
    again = confirm(client_a, document, **body)

    assert first.status_code == 200
    assert again.status_code == 200
    assert again.json()["label_ids"] == [str(bank.id)]
    with workspace_context(ws_a.id):
        # The repeat ran no rules and wrote nothing.
        assert RuleRun.objects.count() == 1
        assert DocumentEvent.objects.filter(kind="confirmed").count() == 1


def test_old_app_confirm_keeps_auto_applied_labels(client_a: Client, ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER, status="processing")
    process(ws_a, document)

    # The 1b app does not know `sets_organisation` and its lists.
    r = confirm(client_a, document)

    assert r.status_code == 200
    assert r.json()["label_ids"] == [str(auto.id)]
    with workspace_context(ws_a.id):
        assert Document.objects.get(id=document.id).confirmed_organisation is None


def test_job_with_another_workspaces_id_changes_nothing(ws_a: Workspace, ws_b: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, LETTER, status="processing")

    jobs.run_job(ws_b.id, document.id, attempts=0, run=process_without_ai)
    jobs.run_job(ws_b.id, document.id, attempts=0, run=failing_run)

    with workspace_context(ws_a.id):
        assert Document.objects.get(id=document.id).status == "processing"
        assert not RuleRun.objects.exists()
        assert not DocumentSuggestion.objects.exists()
        assert not DocumentEvent.objects.exists()
    assert label_ids(ws_a, document) == set()


def process_without_ai(workspace_id: UUID, document_id: UUID) -> None:
    run_pipeline(workspace_id, document_id, provider_factory=lambda _: None)


def failing_run(workspace_id: UUID, document_id: UUID) -> None:
    raise PermanentError("ai_rejected")

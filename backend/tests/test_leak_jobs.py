"""Jobs and rule runs see what their actor sees (S 4.10, R6, R7, plan Task 21).

A rule test and a manual rule run act as the member who asked: they count and touch
only the letters that member may see. The rules that run on their own (after
processing, after confirming) run as the system and reach every letter. A job without
an actor sees no letter at all.
"""

import uuid
from typing import TYPE_CHECKING

from django.db import transaction
from django.test import Client

from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentPage,
    DocumentSuggestion,
    Extraction,
)
from apps.labels.models import Label
from apps.rules import jobs, triggers
from apps.rules.models import Rule, RuleRun
from apps.tasks.models import Task
from apps.workspaces.context import NOBODY, SYSTEM
from apps.workspaces.models import Membership
from tests.helpers import post_json
from tests.rules_support import add_label, make_rule, text_word
from tests.visibility_support import Office, as_actor, office

if TYPE_CHECKING:
    from django.db.models import Model

WORD = "Kikkerpoel"
ON = ("finance_letter", "hr_letter")


def _office_with_word() -> tuple[Office, Label]:
    """`office()` whose Finance and HR letters name `WORD`, and a fresh label."""
    o = office()
    with as_actor(o.workspace, SYSTEM):
        Document.objects.filter(id__in=[o.letters[n].id for n in ON]).update(
            ocr_text=f"Brief van {WORD} over de rekening."
        )
        label = Label.objects.create(workspace=o.workspace, name="Zwaluwstaart")
    return o, label


def _auto_rule(o: Office, label: Label) -> Rule:
    return make_rule(
        o.workspace, text_word(WORD), actions=[add_label(label)], name="Kikker", mode="auto"
    )


def _labelled(o: Office, label: Label) -> set[str]:
    with as_actor(o.workspace, SYSTEM):
        ids = DocumentLabel.objects.filter(label=label).values_list("document_id", flat=True)
        return {o.name_of(i) for i in ids}


def _runs_of(o: Office, rule: Rule) -> set[str]:
    with as_actor(o.workspace, SYSTEM):
        ids = RuleRun.objects.filter(rule=rule).values_list("document_id", flat=True)
        return {o.name_of(i) for i in ids if i is not None}


def _run(o: Office, rule: Rule, starter_id: object) -> None:
    jobs.apply_rule_to_existing(
        workspace_id=str(o.workspace.id), rule_id=str(rule.id), user_id=str(starter_id)
    )


def test_rule_test_counts_only_visible_letters(client: Client) -> None:
    o, label = _office_with_word()
    body = {
        "name": "Kikker",
        "conditions": [{"field": "text", "op": "contains_word", "value": WORD}],
        "actions": [{"type": "add_label", "label_id": str(label.id)}],
    }
    client.force_login(o.admin)

    hidden = post_json(client, f"/workspaces/{o.workspace.id}/rules/test", body)
    assert hidden.status_code == 200, hidden.json()
    assert (hidden.json()["total_count"], hidden.json()["matched_count"]) == (0, 0)
    assert hidden.json()["documents"] == []

    o.state(e=False, d=True, a=True)
    seen = post_json(client, f"/workspaces/{o.workspace.id}/rules/test", body).json()
    assert seen["total_count"] == len(o.visible(o.admin.id))
    assert seen["matched_count"] == 2
    assert {o.name_of(uuid.UUID(d["id"])) for d in seen["documents"]} == set(ON)


def test_manual_run_touches_only_letters_the_starter_sees() -> None:
    o, label = _office_with_word()
    rule = _auto_rule(o, label)
    with as_actor(o.workspace, SYSTEM):
        events = DocumentEvent.objects.filter(document=o.letters["finance_letter"]).count()

    _run(o, rule, o.admin.id)  # Anne, admin, does not read all post: she sees none of it

    assert _labelled(o, label) == set()
    assert _runs_of(o, rule) == set()
    with as_actor(o.workspace, SYSTEM):
        assert DocumentEvent.objects.filter(document=o.letters["finance_letter"]).count() == (
            events
        )

    _run(o, rule, o.hr.id)  # Hans sees HR's letter only
    assert _labelled(o, label) == {"hr_letter"}
    assert _runs_of(o, rule) == {"hr_letter"}


def test_automatic_rules_still_reach_every_letter() -> None:
    o, label = _office_with_word()
    _auto_rule(o, label)
    with as_actor(o.workspace, SYSTEM), transaction.atomic():
        letter = Document.objects.select_for_update(no_key=True).get(
            id=o.letters["finance_letter"].id
        )
        triggers.on_processed(letter)
    assert _labelled(o, label) == {"finance_letter"}


def test_job_with_an_empty_actor_sees_no_document() -> None:
    o = office()
    tables: list[type[Model]] = [
        Document,
        DocumentAddressee,
        DocumentEvent,
        DocumentLabel,
        DocumentPage,
        DocumentSuggestion,
        Extraction,
        RuleRun,
        Task,
    ]
    for state in ({"e": False, "d": True, "a": False}, {"e": True, "d": False, "a": True}):
        o.state(**state)
        with as_actor(o.workspace, NOBODY):
            for model in tables:
                assert model._default_manager.count() == 0, (state, model.__name__)
        with as_actor(o.workspace, SYSTEM):
            assert Document.objects.count() == len(o.letters)


def test_member_removed_between_enqueue_and_run_writes_nothing() -> None:
    o, label = _office_with_word()
    rule = _auto_rule(o, label)
    with as_actor(o.workspace, SYSTEM):
        Membership.objects.filter(workspace=o.workspace, user=o.hr).delete()

    _run(o, rule, o.hr.id)

    assert _labelled(o, label) == set()
    assert _runs_of(o, rule) == set()


def test_rule_list_counts_only_visible_runs(client: Client) -> None:
    """Every letter of the office has one proposed run of the rule "Regel"."""
    o = office()
    url = f"/api/v1/workspaces/{o.workspace.id}/rules"

    def proposed(user_name: str) -> int:
        client.force_login(o.users[user_name])
        response = client.get(url)
        assert response.status_code == 200
        (rule,) = [r for r in response.json() if r["name"] == "Regel"]
        count: int = rule["proposed_count"]
        return count

    assert proposed("admin") == 0
    o.state(e=False, d=True, a=True)
    assert proposed("admin") == len(o.visible(o.admin.id)) == len(o.letters) - 1

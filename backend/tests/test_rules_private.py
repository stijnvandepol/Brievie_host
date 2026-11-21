"""Rules and private letters (plan Task 32, PB T10 and T19): automatic actions never widen
the readers, and the rule action "privé maken"."""

import uuid

from django.test import Client

from apps.accounts.models import AccountNotice, User
from apps.documents.models import (
    Document,
    DocumentAddressee,
    DocumentEvent,
    DocumentLabel,
    DocumentSuggestion,
)
from apps.labels.models import Label
from apps.rules import definition, jobs, triggers
from apps.rules.engine import Action, DocumentFacts, RuleSpec, evaluate
from apps.rules.models import Rule, RuleRun
from apps.workspaces.context import SYSTEM
from apps.workspaces.models import Workspace
from tests.rules_support import add_addressee, add_label, make_rule, remove_rules, text_word
from tests.visibility_support import Home, as_actor, home, private_letter, readers

MAKE_PRIVATE: definition.ActionIn = ("make_private", None, None)
JSON = "application/json"


def _facts(*, restricted: bool = False) -> DocumentFacts:
    return DocumentFacts(
        text="Uw huisarts schrijft",
        sender="",
        document_type="",
        source="scan",
        label_ids=frozenset(),
        addressee_ids=frozenset(),
        restricted=restricted,
    )


def _spec(*actions: Action, mode: str = "auto") -> RuleSpec:
    return RuleSpec(
        id=uuid.uuid4(),
        name="Zorg",
        match="all",
        conditions=(text_word("huisarts"),),
        actions=actions,
        mode=mode,
        stop=False,
    )


# The engine (pure).


def test_make_private_is_planned_for_an_auto_rule_only() -> None:
    auto = evaluate(_facts(), [_spec(Action("make_private", None))]).planned
    assert [(p.action, p.mode, p.target_id) for p in auto] == [("make_private", "auto", None)]
    proposing = evaluate(_facts(), [_spec(Action("make_private", None), mode="propose")])
    assert proposing.planned == ()


def test_make_private_not_planned_on_a_restricted_letter() -> None:
    planned = evaluate(_facts(restricted=True), [_spec(Action("make_private", None))]).planned
    assert planned == ()


def test_addressee_after_make_private_in_the_same_run_is_proposed() -> None:
    person = uuid.uuid4()
    planned = evaluate(
        _facts(),
        [_spec(Action("make_private", None)), _spec(Action("add_addressee", person))],
    ).planned
    assert [(p.action, p.mode) for p in planned] == [
        ("make_private", "auto"),
        ("add_addressee", "propose"),
    ]


def test_auto_label_on_a_restricted_letter_stays_automatic() -> None:
    label = uuid.uuid4()
    planned = evaluate(_facts(restricted=True), [_spec(Action("add_label", label))]).planned
    assert [(p.action, p.mode) for p in planned] == [("add_label", "auto")]


# The definition and the API.


def _rule_body(_h: Home, mode: str, actions: list[dict[str, object]]) -> dict[str, object]:
    return {
        "name": "Zorg privé",
        "mode": mode,
        "conditions": [{"field": "text", "op": "contains_word", "value": "huisarts"}],
        "actions": actions,
    }


def _rules_url(workspace: Workspace) -> str:
    return f"/api/v1/workspaces/{workspace.id}/rules"


PRIVATE_ACTION: dict[str, object] = {"type": "make_private", "label_id": None, "person_id": None}


def test_make_private_in_a_propose_rule_is_422(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    r = client.post(_rules_url(h.workspace), _rule_body(h, "propose", [PRIVATE_ACTION]), JSON)
    assert r.status_code == 422
    assert "automatisch" in r.json()["message"]


def test_make_private_with_a_target_is_422(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    with as_actor(h.workspace, SYSTEM):
        label = Label.objects.filter(workspace=h.workspace).first()
    assert label is not None
    action: dict[str, object] = {**PRIVATE_ACTION, "label_id": str(label.id)}
    r = client.post(_rules_url(h.workspace), _rule_body(h, "auto", [action]), JSON)
    assert r.status_code == 422


def test_switching_a_private_rule_to_propose_is_422_and_changes_nothing(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    created = client.post(_rules_url(h.workspace), _rule_body(h, "auto", [PRIVATE_ACTION]), JSON)
    assert created.status_code == 201, created.json()
    rule_id = created.json()["id"]
    r = client.put(
        f"{_rules_url(h.workspace)}/{rule_id}",
        _rule_body(h, "propose", [PRIVATE_ACTION]),
        content_type=JSON,
    )
    assert r.status_code == 422
    with as_actor(h.workspace, SYSTEM):
        assert Rule.objects.get(id=rule_id).mode == "auto"


def test_definition_keeps_version_1() -> None:
    stored = definition.to_json("all", [text_word("huisarts")], [MAKE_PRIVATE])
    assert stored["version"] == 1
    assert stored["actions"] == [PRIVATE_ACTION]


def test_rule_with_make_private_round_trips_through_a_1c_client(client: Client) -> None:
    h = home()
    client.force_login(h.anne)
    created = client.post(_rules_url(h.workspace), _rule_body(h, "auto", [PRIVATE_ACTION]), JSON)
    rule = created.json()
    with as_actor(h.workspace, SYSTEM):
        before = Rule.objects.get(id=rule["id"]).definition
    old_fields = ("name", "active", "mode", "stop", "match", "conditions", "actions")
    r = client.put(
        f"{_rules_url(h.workspace)}/{rule['id']}",
        {key: rule[key] for key in old_fields},
        content_type=JSON,
    )
    assert r.status_code == 200, r.json()
    with as_actor(h.workspace, SYSTEM):
        assert Rule.objects.get(id=rule["id"]).definition == before


# The executor.


def _letter(h: Home, *, scanned_by: User | None = None, status: str = "needs_review") -> Document:
    with as_actor(h.workspace, SYSTEM):
        return Document.objects.create(
            workspace=h.workspace,
            client_id=uuid.uuid4(),
            page_count=1,
            ocr_text="Uw huisarts schrijft",
            status=status,
            scanned_by=scanned_by,
        )


def _process(h: Home, doc: Document) -> None:
    with as_actor(h.workspace, SYSTEM):
        locked = Document.objects.select_for_update(no_key=True).get(id=doc.id)
        triggers.on_processed(locked)


def test_auto_rule_makes_letter_private_in_the_pipeline() -> None:
    h = home()
    remove_rules(h.workspace)
    rule = make_rule(h.workspace, text_word("huisarts"), actions=[MAKE_PRIVATE], mode="auto")
    doc = _letter(h, scanned_by=h.bob)
    _process(h, doc)
    assert readers(doc) == {("Bob", "share")}
    with as_actor(h.workspace, SYSTEM):
        assert RuleRun.objects.get(rule=rule, document=doc).outcome == "applied"
        line = DocumentEvent.objects.get(document=doc, kind="made_private")
    assert (line.origin, line.rule_id) == ("rule", rule.id)


def test_manual_run_makes_private_and_the_starter_keeps_sight() -> None:
    h = home()
    remove_rules(h.workspace)
    rule = make_rule(h.workspace, text_word("huisarts"), actions=[MAKE_PRIVATE], mode="auto")
    doc = _letter(h, scanned_by=h.bob, status="done")
    jobs.apply_rule_batch(h.workspace.id, rule.id, user_id=h.anne.id, after=None)
    assert readers(doc) == {("Bob", "share"), ("Anne", "share")}


def test_manual_run_on_a_letter_nobody_could_read_leaves_it_public() -> None:
    h = home()
    remove_rules(h.workspace)
    rule = make_rule(h.workspace, text_word("huisarts"), actions=[MAKE_PRIVATE], mode="auto")
    doc = _letter(h)
    _process(h, doc)
    with as_actor(h.workspace, SYSTEM):
        assert Document.objects.get(id=doc.id).visibility == "workspace"
        assert not RuleRun.objects.filter(rule=rule).exists()
        assert DocumentEvent.objects.filter(document=doc, kind="private_skipped").count() == 1
    assert AccountNotice.objects.filter(user=h.anne, code="private_without_readers").count() == 1


def _private_on_oma(h: Home) -> Document:
    doc = private_letter(h.workspace, addressees=[h.persons["Oma"]], shared_with=[h.bob])
    with as_actor(h.workspace, SYSTEM):
        Document.objects.filter(id=doc.id).update(ocr_text="Uw huisarts schrijft")
    return doc


def test_auto_addressee_rule_on_private_letter_leaves_a_suggestion() -> None:
    h = home()
    remove_rules(h.workspace)
    make_rule(
        h.workspace,
        text_word("huisarts"),
        actions=[add_addressee(h.persons["Carla"].id)],
        mode="auto",
    )
    doc = _private_on_oma(h)
    before = readers(doc)
    _process(h, doc)
    with as_actor(h.workspace, SYSTEM):
        assert not DocumentAddressee.objects.filter(
            document=doc, person=h.persons["Carla"]
        ).exists()
        (row,) = DocumentSuggestion.objects.filter(document=doc, person=h.persons["Carla"])
    assert (row.state, row.origin) == ("open", "rule")
    assert readers(doc) == before


def test_manual_run_does_not_add_addressee_to_private_letter() -> None:
    h = home()
    remove_rules(h.workspace)
    rule = make_rule(
        h.workspace,
        text_word("huisarts"),
        actions=[add_addressee(h.persons["Carla"].id)],
        mode="auto",
    )
    doc = _private_on_oma(h)
    jobs.apply_rule_batch(h.workspace.id, rule.id, user_id=h.bob.id, after=None)
    with as_actor(h.workspace, SYSTEM):
        assert not DocumentAddressee.objects.filter(
            document=doc, person=h.persons["Carla"]
        ).exists()
    assert ("Carla", "addressee") not in readers(doc)


def test_label_rule_still_labels_a_private_letter() -> None:
    h = home()
    remove_rules(h.workspace)
    with as_actor(h.workspace, SYSTEM):
        label = Label.objects.create(workspace=h.workspace, name="Dokter")
    make_rule(h.workspace, text_word("huisarts"), actions=[add_label(label)], mode="auto")
    doc = _private_on_oma(h)
    _process(h, doc)
    with as_actor(h.workspace, SYSTEM):
        assert DocumentLabel.objects.filter(document=doc, label=label).exists()


def test_that_suggestion_is_visible_only_to_readers_and_accepting_it_adds_a_reader(
    client: Client,
) -> None:
    h = home()
    remove_rules(h.workspace)
    make_rule(
        h.workspace,
        text_word("huisarts"),
        actions=[add_addressee(h.persons["Carla"].id)],
        mode="auto",
    )
    doc = _private_on_oma(h)
    _process(h, doc)
    with as_actor(h.workspace, h.anne.id):
        assert DocumentSuggestion.objects.filter(document=doc).count() == 0
    with as_actor(h.workspace, h.bob.id):
        (row,) = DocumentSuggestion.objects.filter(document=doc, state="open")
    client.force_login(h.bob)
    r = client.post(
        f"/api/v1/workspaces/{h.workspace.id}/documents/{doc.id}/suggestions/{row.id}/accept",
        content_type=JSON,
    )
    assert r.status_code == 200, r.json()
    assert ("Carla", "addressee") in readers(doc)


def test_rules_at_confirm_leave_an_addressee_suggestion_after_zorg(client: Client) -> None:
    h = home()
    remove_rules(h.workspace)
    make_rule(
        h.workspace,
        text_word("huisarts"),
        actions=[add_addressee(h.persons["Carla"].id)],
        mode="auto",
    )
    doc = _letter(h, scanned_by=h.bob)
    with as_actor(h.workspace, SYSTEM):
        zorg = Label.objects.get(workspace=h.workspace, system_key="zorg")
    client.force_login(h.bob)
    r = client.post(
        f"/api/v1/workspaces/{h.workspace.id}/documents/{doc.id}/confirm",
        {
            "sender": "Huisarts",
            "document_type": "",
            "summary": "",
            "summary_by_ai": False,
            "tasks": [],
            "sets_organisation": True,
            "label_ids": [str(zorg.id)],
            "addressee_ids": [],
        },
        content_type=JSON,
    )
    assert r.status_code == 200, r.json()
    with as_actor(h.workspace, SYSTEM):
        assert Document.objects.get(id=doc.id).visibility == "restricted"
        assert not DocumentAddressee.objects.filter(document=doc).exists()
        assert DocumentSuggestion.objects.filter(
            document=doc, person=h.persons["Carla"], state="open"
        ).exists()

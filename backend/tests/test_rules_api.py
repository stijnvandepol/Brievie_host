import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

import pytest
from django.test import Client
from procrastinate.contrib.django.models import ProcrastinateJob

from apps.documents import organise
from apps.documents.models import Document, DocumentEvent, DocumentLabel, DocumentSuggestion
from apps.labels.models import Label
from apps.rules import engine, triggers
from apps.rules.engine import DocumentFacts, Evaluation, RuleSpec
from apps.rules.models import Rule, RuleRun
from apps.rules.templates import rule_templates
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Membership, Workspace
from tests.documents_support import admin_of, make_person
from tests.factories import UserFactory
from tests.helpers import post_json
from tests.rules_support import add_label, make_document, make_rule, remove_rules, text_word

if TYPE_CHECKING:
    from tests.helpers import Response

LETTER = "Uitnodiging voor de APK van uw auto met kenteken AB-12-CD."


def rules_url(workspace_id: object) -> str:
    return f"/api/v1/workspaces/{workspace_id}/rules"


def put_json(client: Client, url: str, body: dict[str, object]) -> "Response":
    return client.put(url, body, content_type="application/json")


def label(workspace: Workspace, key: str) -> Label:
    with workspace_context(workspace.id):
        return Label.objects.get(workspace=workspace, system_key=key)


def rule_body(label_id: object, **fields: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "APK",
        "conditions": [{"field": "text", "op": "contains_word", "value": "APK"}],
        "actions": [{"type": "add_label", "label_id": str(label_id)}],
    }
    body.update(fields)
    return body


@pytest.fixture
def client_a(client: Client, ws_a: Workspace) -> Client:
    client.force_login(admin_of(ws_a))
    return client


def test_create_appends_and_lists_in_order(client_a: Client, ws_a: Workspace) -> None:
    auto = label(ws_a, "auto")
    before = client_a.get(rules_url(ws_a.id)).json()

    r = post_json(client_a, f"/workspaces/{ws_a.id}/rules", rule_body(auto.id, mode="auto"))

    assert r.status_code == 201
    created = r.json()
    assert set(created) == {
        "id",
        "position",
        "name",
        "active",
        "mode",
        "stop",
        "match",
        "conditions",
        "actions",
        "system_key",
        "applied_count",
        "proposed_count",
    }
    assert (created["name"], created["mode"], created["match"], created["system_key"]) == (
        "APK",
        "auto",
        "all",
        "",
    )
    assert created["conditions"] == [{"field": "text", "op": "contains_word", "value": "APK"}]
    assert created["actions"] == [
        {"type": "add_label", "label_id": str(auto.id), "person_id": None}
    ]
    assert (created["applied_count"], created["proposed_count"]) == (0, 0)
    rows = client_a.get(rules_url(ws_a.id)).json()
    assert [row["id"] for row in rows] == [row["id"] for row in before] + [created["id"]]
    assert created["position"] == max(row["position"] for row in before) + 1


def test_list_counts_runs(client_a: Client, ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)])
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        for outcome in ("applied", "applied", "proposed"):
            RuleRun.objects.create(
                workspace=ws_a, rule=rule, document=document, trigger="manual", outcome=outcome
            )

    [row] = client_a.get(rules_url(ws_a.id)).json()

    assert (row["applied_count"], row["proposed_count"]) == (2, 1)


@pytest.mark.parametrize(
    "change",
    [
        {"conditions": [{"field": "kleur", "op": "is", "value": "rood"}]},
        {"conditions": [{"field": "text", "op": "contains_word", "value": "A"}]},
        {"actions": [{"type": "add_label"}]},
        {"mode": "soms"},
        {"name": "   "},
    ],
)
def test_invalid_rule_is_422_with_a_dutch_sentence(
    client_a: Client, ws_a: Workspace, change: dict[str, object]
) -> None:
    count = len(client_a.get(rules_url(ws_a.id)).json())

    r = post_json(
        client_a, f"/workspaces/{ws_a.id}/rules", rule_body(label(ws_a, "auto").id, **change)
    )

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"
    assert r.json()["message"].endswith(".")
    assert len(client_a.get(rules_url(ws_a.id)).json()) == count


def test_action_with_archived_person_is_422(client_a: Client, ws_a: Workspace) -> None:
    person = make_person(ws_a, "Oma", archived=True)
    body = rule_body(
        label(ws_a, "auto").id, actions=[{"type": "add_addressee", "person_id": str(person.id)}]
    )

    r = post_json(client_a, f"/workspaces/{ws_a.id}/rules", body)

    assert r.status_code == 422


def test_label_of_another_workspace_is_422(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    r = post_json(client_a, f"/workspaces/{ws_a.id}/rules", rule_body(label(ws_b, "bank").id))

    assert r.status_code == 422


def test_name_is_masked(client_a: Client, ws_a: Workspace) -> None:
    body = rule_body(label(ws_a, "auto").id, name=" APK 111222333 ")

    r = post_json(client_a, f"/workspaces/{ws_a.id}/rules", body)

    assert r.json()["name"] == "APK [BSN]"


def test_at_most_200_rules(client_a: Client, ws_a: Workspace) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    with workspace_context(ws_a.id):
        Rule.objects.bulk_create(
            Rule(workspace=ws_a, position=i, name=f"R{i}", definition={}) for i in range(1, 201)
        )

    r = post_json(client_a, f"/workspaces/{ws_a.id}/rules", rule_body(auto.id))

    assert r.status_code == 422
    assert r.json()["code"] == "validation_error"


def test_update_and_delete_a_rule(client_a: Client, ws_a: Workspace) -> None:
    auto = label(ws_a, "auto")
    created = post_json(client_a, f"/workspaces/{ws_a.id}/rules", rule_body(auto.id)).json()
    url = f"{rules_url(ws_a.id)}/{created['id']}"

    r = put_json(client_a, url, rule_body(auto.id, name="Auto", active=False, stop=True))

    assert r.status_code == 200
    updated = r.json()
    assert (updated["name"], updated["active"], updated["stop"]) == ("Auto", False, True)
    assert updated["position"] == created["position"]

    assert client_a.delete(url).status_code == 204
    with workspace_context(ws_a.id):
        assert not Rule.objects.filter(id=created["id"]).exists()


def test_member_lists_but_cannot_create(client: Client, ws_a: Workspace) -> None:
    member = UserFactory.create()
    Membership.objects.create(user=member, workspace=ws_a, role="member")
    client.force_login(member)

    assert client.get(rules_url(ws_a.id)).status_code == 200
    r = post_json(client, f"/workspaces/{ws_a.id}/rules", rule_body(label(ws_a, "auto").id))
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden"


def test_viewer_cannot_read_or_change_rules(client: Client, ws_a: Workspace) -> None:
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)])
    viewer = UserFactory.create()
    Membership.objects.create(user=viewer, workspace=ws_a, role="viewer")
    client.force_login(viewer)
    url = rules_url(ws_a.id)

    for r in (
        client.get(url),
        client.get(f"{url}/templates"),
        post_json(client, f"/workspaces/{ws_a.id}/rules", rule_body(auto.id)),
        put_json(client, f"{url}/order", {"rule_ids": [str(rule.id)]}),
        post_json(client, f"/workspaces/{ws_a.id}/rules/test", rule_body(auto.id)),
        put_json(client, f"{url}/{rule.id}", rule_body(auto.id)),
        client.delete(f"{url}/{rule.id}"),
        post_json(client, f"/workspaces/{ws_a.id}/rules/{rule.id}/run"),
    ):
        assert r.status_code == 403
        assert r.json() == {
            "code": "forbidden",
            "message": "Je mag dit niet doen in deze werkruimte.",
        }


def test_member_lists_the_templates(client: Client, ws_a: Workspace) -> None:
    member = UserFactory.create()
    Membership.objects.create(user=member, workspace=ws_a, role="member")
    client.force_login(member)

    r = client.get(f"{rules_url(ws_a.id)}/templates")

    assert r.status_code == 200
    rows = r.json()
    assert [row["key"] for row in rows] == [t.key for t in rule_templates()]
    first = rows[0]
    assert set(first) == {
        "key",
        "kinds",
        "title",
        "description",
        "tip",
        "name",
        "mode",
        "match",
        "conditions",
        "actions",
    }
    assert first["conditions"][0] == {
        "field": "sender",
        "op": "contains_word",
        "value": "",
        "example": "Zilveren Kruis",
        "hint": "Typ de naam van de afzender, zoals die op de brief staat.",
    }


def test_every_template_filled_in_can_be_tested_and_stores_nothing(
    client_a: Client, ws_a: Workspace
) -> None:
    """The gallery's "try before saving": the test endpoint takes each filled-in template."""
    auto = label(ws_a, "auto")
    person = make_person(ws_a, "Sam")
    make_document(ws_a)
    before = client_a.get(rules_url(ws_a.id)).json()

    for row in client_a.get(f"{rules_url(ws_a.id)}/templates").json():
        body = {
            "name": row["name"],
            "mode": row["mode"],
            "match": row["match"],
            "conditions": [
                {"field": c["field"], "op": c["op"], "value": c["value"] or c["example"]}
                for c in row["conditions"]
            ],
            "actions": [
                {"type": a["type"], "label_id": str(auto.id)}
                if a["type"] == "add_label"
                else {"type": a["type"], "person_id": str(person.id)}
                for a in row["actions"]
            ],
        }
        r = post_json(client_a, f"/workspaces/{ws_a.id}/rules/test", body)
        assert r.status_code == 200, (row["key"], r.json())

    assert client_a.get(rules_url(ws_a.id)).json() == before


def test_a_suggested_template_label_is_in_the_start_set(ws_a: Workspace) -> None:
    for template in rule_templates():
        if Workspace.Kind.HOME not in template.kinds:
            continue
        for action in template.actions:
            if action.label_key:
                assert label(ws_a, action.label_key).name == action.label_name


def test_reorder_sets_the_order(client_a: Client, ws_a: Workspace) -> None:
    ids = [row["id"] for row in client_a.get(rules_url(ws_a.id)).json()]

    r = put_json(client_a, f"{rules_url(ws_a.id)}/order", {"rule_ids": ids[::-1]})

    assert r.status_code == 200
    assert [row["id"] for row in r.json()] == ids[::-1]
    assert [row["id"] for row in client_a.get(rules_url(ws_a.id)).json()] == ids[::-1]


def test_reorder_needs_every_rule_exactly_once(client_a: Client, ws_a: Workspace) -> None:
    ids = [row["id"] for row in client_a.get(rules_url(ws_a.id)).json()]
    assert len(ids) >= 2

    for rule_ids in (ids[1:], [*ids, ids[0]], [*ids[1:], str(uuid.uuid4())]):
        r = put_json(client_a, f"{rules_url(ws_a.id)}/order", {"rule_ids": rule_ids})
        assert r.status_code == 422
        assert r.json()["code"] == "validation_error"

    assert [row["id"] for row in client_a.get(rules_url(ws_a.id)).json()] == ids


def test_test_rule_counts_condition_matches_and_stores_nothing(
    client_a: Client, ws_a: Workspace
) -> None:
    auto = label(ws_a, "auto")
    hits = [make_document(ws_a, LETTER), make_document(ws_a, "Uw APK verloopt.")]
    make_document(ws_a, "Een brief van de bank.")
    rules_before = len(client_a.get(rules_url(ws_a.id)).json())

    r = post_json(client_a, f"/workspaces/{ws_a.id}/rules/test", rule_body(auto.id, mode="auto"))

    assert r.status_code == 200
    out = r.json()
    assert (out["total_count"], out["checked_count"], out["matched_count"]) == (3, 3, 2)
    assert {d["id"] for d in out["documents"]} == {str(d.id) for d in hits}
    assert len(client_a.get(rules_url(ws_a.id)).json()) == rules_before
    with workspace_context(ws_a.id):
        assert not RuleRun.objects.exists()
        assert not DocumentLabel.objects.exists()
        assert not DocumentSuggestion.objects.exists()
        assert not DocumentEvent.objects.exists()


def test_test_rule_counts_a_document_that_already_has_the_label(
    client_a: Client, ws_a: Workspace
) -> None:
    auto = label(ws_a, "auto")
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        DocumentLabel.objects.create(workspace=ws_a, document=document, label=auto)

    out = post_json(client_a, f"/workspaces/{ws_a.id}/rules/test", rule_body(auto.id)).json()

    assert out["matched_count"] == 1
    assert [d["label_ids"] for d in out["documents"]] == [[str(auto.id)]]


def test_test_rule_skips_processing_documents_and_reports_total(
    client_a: Client, ws_a: Workspace
) -> None:
    make_document(ws_a, LETTER)
    make_document(ws_a, LETTER, status="processing")

    out = post_json(
        client_a, f"/workspaces/{ws_a.id}/rules/test", rule_body(label(ws_a, "auto").id)
    ).json()

    assert (out["total_count"], out["checked_count"], out["matched_count"]) == (1, 1, 1)


def test_test_rule_checks_the_newest_500(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("apps.rules.services.TEST_LIMIT", 2)
    monkeypatch.setattr("apps.rules.services.TEST_SHOWN", 1)
    make_document(ws_a, LETTER)
    newer = [make_document(ws_a, LETTER), make_document(ws_a, LETTER)]

    out = post_json(
        client_a, f"/workspaces/{ws_a.id}/rules/test", rule_body(label(ws_a, "auto").id)
    ).json()

    assert (out["total_count"], out["checked_count"], out["matched_count"]) == (3, 2, 2)
    assert [d["id"] for d in out["documents"]] == [str(newer[1].id)]


def _record_texts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The text of every document the test hands to the rules engine."""
    seen: list[str] = []
    real = engine.evaluate

    def spy(facts: DocumentFacts, rules: Sequence[RuleSpec]) -> Evaluation:
        seen.append(facts.text)
        return real(facts, rules)

    monkeypatch.setattr("apps.rules.services.evaluate", spy)
    return seen


def test_test_rule_stops_at_the_text_budget_and_says_how_many_it_checked(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("apps.rules.services.TEST_TEXT_CHARS", 100)
    monkeypatch.setattr("apps.rules.services.TEST_TEXT_BUDGET", 250)
    seen = _record_texts(monkeypatch)
    for _ in range(5):
        make_document(ws_a, LETTER.ljust(100, "."))

    out = post_json(
        client_a, f"/workspaces/{ws_a.id}/rules/test", rule_body(label(ws_a, "auto").id)
    ).json()

    assert (out["total_count"], out["checked_count"], out["matched_count"]) == (5, 2, 2)
    assert len(seen) == 2
    assert sum(len(text) for text in seen) <= 250


def test_test_rule_reads_only_the_start_of_a_long_document(
    client_a: Client, ws_a: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("apps.rules.services.TEST_TEXT_CHARS", 100)
    seen = _record_texts(monkeypatch)
    early = make_document(ws_a, LETTER + " " + "x" * 500)
    make_document(ws_a, "y " * 100 + LETTER)

    out = post_json(
        client_a, f"/workspaces/{ws_a.id}/rules/test", rule_body(label(ws_a, "auto").id)
    ).json()

    assert (out["total_count"], out["checked_count"], out["matched_count"]) == (2, 2, 1)
    assert [d["id"] for d in out["documents"]] == [str(early.id)]
    assert sorted(len(text) for text in seen) == [100, 100]


def test_invalid_test_rule_is_422(client_a: Client, ws_a: Workspace) -> None:
    body = rule_body(label(ws_a, "auto").id, match="soms")

    r = post_json(client_a, f"/workspaces/{ws_a.id}/rules/test", body)

    assert r.status_code == 422


def test_run_rule_queues_job_with_ids_only(client_a: Client, ws_a: Workspace) -> None:
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))])

    r = post_json(client_a, f"/workspaces/{ws_a.id}/rules/{rule.id}/run")

    assert r.status_code == 202
    assert r.json() == {"queued": True}
    job = ProcrastinateJob.objects.get(task_name="apply_rule_to_existing")
    assert job.queue_name == "pipeline"
    assert job.args == {"workspace_id": str(ws_a.id), "rule_id": str(rule.id)}


def test_running_a_rule_twice_queues_one_job(client_a: Client, ws_a: Workspace) -> None:
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))])
    url = f"/workspaces/{ws_a.id}/rules/{rule.id}/run"

    first = post_json(client_a, url)
    second = post_json(client_a, url)

    assert (first.status_code, second.status_code) == (202, 202)
    assert second.json() == {"queued": True}
    assert ProcrastinateJob.objects.filter(task_name="apply_rule_to_existing").count() == 1


def test_run_inactive_rule_is_409(client_a: Client, ws_a: Workspace) -> None:
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label(ws_a, "auto"))], active=False)

    r = post_json(client_a, f"/workspaces/{ws_a.id}/rules/{rule.id}/run")

    assert r.status_code == 409
    assert r.json()["code"] == "rule_inactive"
    assert not ProcrastinateJob.objects.filter(task_name="apply_rule_to_existing").exists()


def test_delete_rule_keeps_history_names(client_a: Client, ws_a: Workspace) -> None:
    auto = label(ws_a, "auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(auto)], name="APK-regel")
    document = make_document(ws_a, LETTER)
    with workspace_context(ws_a.id):
        organise.add_label(document, auto, actor=None, origin="rule", rule=rule)

    assert client_a.delete(f"{rules_url(ws_a.id)}/{rule.id}").status_code == 204

    with workspace_context(ws_a.id):
        event = DocumentEvent.objects.get(document=document)
    assert (event.rule_id, event.rule_name) == (None, "APK-regel")


def test_other_workspace_is_404_on_every_rule_endpoint(
    client_a: Client, ws_a: Workspace, ws_b: Workspace
) -> None:
    auto = label(ws_a, "auto")
    stranger = make_rule(ws_b, text_word("APK"), actions=[add_label(label(ws_b, "bank"))])
    own = f"{rules_url(ws_a.id)}/{stranger.id}"

    for r in (
        put_json(client_a, own, rule_body(auto.id)),
        client_a.delete(own),
        post_json(client_a, f"/workspaces/{ws_a.id}/rules/{stranger.id}/run"),
        client_a.delete(f"{rules_url(ws_a.id)}/{uuid.uuid4()}"),
    ):
        assert r.status_code == 404
        assert r.json()["code"] == "rule_not_found"

    other = rules_url(ws_b.id)
    for r in (
        client_a.get(other),
        post_json(client_a, f"/workspaces/{ws_b.id}/rules", rule_body(auto.id)),
        put_json(client_a, f"{other}/order", {"rule_ids": [str(stranger.id)]}),
        post_json(client_a, f"/workspaces/{ws_b.id}/rules/test", rule_body(auto.id)),
        put_json(client_a, f"{other}/{stranger.id}", rule_body(auto.id)),
        client_a.delete(f"{other}/{stranger.id}"),
        post_json(client_a, f"/workspaces/{ws_b.id}/rules/{stranger.id}/run"),
    ):
        assert r.status_code == 404
        assert r.json()["code"] == "workspace_not_found"
    with workspace_context(ws_b.id):
        assert Rule.objects.filter(id=stranger.id).exists()


BSN_IN_RULE = "Een BSN bewaren we nooit, dus je kunt het niet in een regel gebruiken."


@pytest.mark.parametrize(
    ("field", "op", "value"),
    [
        ("text", "contains_word", "123456782"),
        ("text", "contains", "111222333"),
        ("sender", "equals", "Belastingdienst 111 222 333"),
        ("text", "contains", "[BSN]"),
    ],
)
def test_a_bsn_in_a_condition_is_refused_on_create_and_test(
    client_a: Client, ws_a: Workspace, field: str, op: str, value: str
) -> None:
    # The finding: BSN 123456782 is in no letter, yet the test matched every letter
    # with any BSN, because both sides were masked to "[BSN]".
    make_document(ws_a, "Burgerservicenummer: [BSN]")
    body = rule_body(
        label(ws_a, "auto").id, conditions=[{"field": field, "op": op, "value": value}]
    )
    for url in (f"/workspaces/{ws_a.id}/rules", f"/workspaces/{ws_a.id}/rules/test"):
        r = post_json(client_a, url, body)
        assert r.status_code == 422, url
        assert r.json() == {
            "code": "validation_error",
            "message": BSN_IN_RULE + " Laat het nummer weg.",
        }


def test_a_rule_masked_before_matches_nothing_and_can_be_switched_off(
    client_a: Client, ws_a: Workspace
) -> None:
    remove_rules(ws_a)
    auto = label(ws_a, "auto")
    # Stored before BSNs were refused: to_json masked the number to "[BSN]".
    old = make_rule(ws_a, text_word("123456782"), actions=[add_label(auto)], mode="auto")
    document = make_document(ws_a, "Burgerservicenummer: [BSN]\nUw aanslag")
    with workspace_context(ws_a.id):
        assert old.definition["conditions"][0]["value"] == "[BSN]"
        triggers.on_confirmed(Document.objects.select_for_update().get(id=document.id))
        assert not DocumentLabel.objects.filter(document=document).exists()
        assert not DocumentSuggestion.objects.filter(document=document).exists()

    body = rule_body(
        auto.id,
        active=False,
        conditions=[{"field": "text", "op": "contains_word", "value": "[BSN]"}],
    )
    r = put_json(client_a, f"{rules_url(ws_a.id)}/{old.id}", body)
    assert r.status_code == 200
    assert r.json()["active"] is False

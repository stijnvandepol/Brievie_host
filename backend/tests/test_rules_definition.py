import logging
import uuid

import pytest

from apps.common.errors import ApiError
from apps.common.privacy import mask_bsns
from apps.labels.models import Label
from apps.people.models import Person
from apps.rules import definition
from apps.rules.definition import to_json, to_spec, validate
from apps.rules.engine import Condition
from apps.rules.facts import active_specs
from apps.rules.models import Rule
from apps.workspaces.context import workspace_context
from apps.workspaces.models import Workspace
from tests.documents_support import make_person
from tests.rules_support import add_label, make_rule, remove_rules, text_word

LABEL = uuid.uuid4()
PERSON = uuid.uuid4()


def check(
    conditions: list[Condition],
    actions: list[definition.ActionIn] | None = None,
    *,
    match: str = "all",
    label_ids: set[uuid.UUID] | None = None,
    person_ids: set[uuid.UUID] | None = None,
) -> None:
    validate(
        match,
        conditions,
        actions if actions is not None else [("add_label", LABEL, None)],
        label_ids=label_ids if label_ids is not None else {LABEL},
        person_ids=person_ids if person_ids is not None else {PERSON},
    )


def refusal(**kwargs: object) -> str:
    with pytest.raises(ApiError) as caught:
        check(**kwargs)  # type: ignore[arg-type]
    assert caught.value.status == 422
    assert caught.value.code == "validation_error"
    return caught.value.message


def test_definition_rejects_unknown_operator_with_dutch_sentence() -> None:
    message = refusal(conditions=[Condition("text", "equals", "APK")])
    assert message == "Een voorwaarde heeft een vergelijking die bij dit veld niet past."


def test_definition_rejects_a_label_id_outside_the_given_set(
    ws_a: Workspace, ws_b: Workspace
) -> None:
    with workspace_context(ws_b.id):
        foreign = Label.objects.filter(workspace=ws_b).first()
    with workspace_context(ws_a.id):
        own = set(Label.objects.filter(workspace=ws_a).values_list("id", flat=True))
    assert foreign is not None

    message = refusal(
        conditions=[text_word("APK")], actions=[("add_label", foreign.id, None)], label_ids=own
    )
    assert "label" in message


@pytest.mark.parametrize(
    ("conditions", "actions"),
    [
        ([], None),
        ([text_word("APK")] * 21, None),
        ([text_word("APK")], []),
        ([text_word("APK")], [("add_label", LABEL, None)] * 11),
        ([text_word(" A ")], None),
        ([text_word("x" * 101)], None),
        ([Condition("sender", "contains", "x" * 201)], None),
        ([Condition("document_type", "is", "letter")], None),
        ([Condition("source", "is", "fax")], None),
        ([Condition("label", "has", str(uuid.uuid4()))], None),
        ([Condition("label", "has", "geen id")], None),
        ([Condition("addressee", "is", str(uuid.uuid4()))], None),
        ([Condition("addressee", "is_empty", str(PERSON))], None),
        ([Condition("colour", "is", "red")], None),
        ([text_word("APK")], [("add_label", None, PERSON)]),
        ([text_word("APK")], [("add_label", LABEL, PERSON)]),
        ([text_word("APK")], [("add_addressee", LABEL, None)]),
        ([text_word("APK")], [("add_addressee", None, uuid.uuid4())]),
        ([text_word("APK")], [("remove_label", LABEL, None)]),
    ],
)
def test_definition_limits(
    conditions: list[Condition], actions: list[definition.ActionIn] | None
) -> None:
    refusal(conditions=conditions, actions=actions)


def test_definition_rejects_unknown_match() -> None:
    refusal(conditions=[text_word("APK")], match="none")


def test_valid_definition_passes() -> None:
    check(
        [
            text_word(" APK "),
            Condition("sender", "equals", "RDW"),
            Condition("document_type", "is", "inspection"),
            Condition("source", "is", "upload"),
            Condition("label", "has_not", str(LABEL)),
            Condition("addressee", "is", str(PERSON)),
            Condition("addressee", "is_empty", ""),
        ],
        [("add_label", LABEL, None), ("add_addressee", None, PERSON)],
        match="any",
    )


def test_archived_person_is_refused(ws_a: Workspace) -> None:
    archived = make_person(ws_a, "Oud", archived=True)
    with workspace_context(ws_a.id):
        active = set(Person.objects.filter(archived_at__isnull=True).values_list("id", flat=True))
    refusal(
        conditions=[text_word("APK")],
        actions=[("add_addressee", None, archived.id)],
        person_ids=active,
    )


def test_definition_masks_bsn_in_values() -> None:
    stored = to_json(
        "all",
        [text_word("  BSN 111222333 "), Condition("label", "has", str(LABEL))],
        [("add_label", LABEL, None)],
    )
    assert stored == {
        "version": 1,
        "match": "all",
        "conditions": [
            {"field": "text", "op": "contains_word", "value": "BSN [BSN]"},
            {"field": "label", "op": "has", "value": str(LABEL)},
        ],
        "actions": [{"type": "add_label", "label_id": str(LABEL), "person_id": None}],
    }


def test_to_spec_reads_back_what_to_json_wrote(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    with workspace_context(ws_a.id):
        label = Label.objects.get(system_key="auto")
    rule = make_rule(ws_a, text_word("APK"), actions=[add_label(label)], name="APK", mode="auto")

    spec = to_spec(rule)

    assert spec is not None
    assert (spec.id, spec.name, spec.match, spec.mode, spec.stop) == (
        rule.id,
        "APK",
        "all",
        "auto",
        False,
    )
    assert spec.conditions == (text_word("APK"),)
    assert [(a.type, a.target_id) for a in spec.actions] == [("add_label", label.id)]


@pytest.mark.parametrize(
    "stored",
    [
        {"version": 99, "match": "all", "conditions": [], "actions": []},
        "geen object",
        {"version": 1, "match": "all", "conditions": [], "actions": []},
        {
            "version": 1,
            "match": "all",
            "conditions": [{"field": "text", "op": "contains", "value": ""}],
            "actions": [{"type": "add_label", "label_id": str(LABEL)}],
        },
        {
            "version": 1,
            "match": "all",
            "conditions": [{"field": "text", "op": "contains", "value": "APK"}],
            "actions": [{"type": "add_label", "label_id": "geen id"}],
        },
    ],
)
def test_broken_definition_is_skipped_not_fatal(
    ws_a: Workspace, stored: object, caplog: pytest.LogCaptureFixture
) -> None:
    remove_rules(ws_a)
    with workspace_context(ws_a.id):
        label = Label.objects.get(system_key="auto")
    broken = make_rule(ws_a, text_word("APK"), actions=[add_label(label)], name="Kapot")
    good = make_rule(ws_a, text_word("APK"), actions=[add_label(label)], name="Goed")
    with workspace_context(ws_a.id):
        Rule.objects.filter(id=broken.id).update(definition=stored)
        broken.refresh_from_db()

        with caplog.at_level(logging.WARNING):
            assert to_spec(broken) is None
            specs = active_specs(ws_a.id)

    assert [spec.id for spec in specs] == [good.id]
    # The log filter masks a run of nine digits that passes the BSN check, also inside an
    # id; a random uuid4 sometimes holds one, so compare with the id as it is logged.
    assert mask_bsns(str(broken.id)) in caplog.text
    assert "Kapot" not in caplog.text


def test_active_specs_are_active_rules_in_position_order(ws_a: Workspace) -> None:
    remove_rules(ws_a)
    with workspace_context(ws_a.id):
        label = Label.objects.get(system_key="auto")
    first = make_rule(ws_a, text_word("APK"), actions=[add_label(label)])
    make_rule(ws_a, text_word("APK"), actions=[add_label(label)], active=False)
    third = make_rule(ws_a, text_word("APK"), actions=[add_label(label)])
    with workspace_context(ws_a.id):
        # Swap positions inside one transaction: the unique position is deferred.
        Rule.objects.filter(id=first.id).update(position=3)
        Rule.objects.filter(id=third.id).update(position=1)

        assert [spec.id for spec in active_specs(ws_a.id)] == [third.id, first.id]


@pytest.mark.parametrize(
    "condition",
    [
        Condition("text", "contains_word", "123456782"),
        Condition("text", "contains", "BSN 111.222.333"),
        Condition("sender", "contains", "Gemeente 111222333"),
        Condition("text", "not_contains_word", "[BSN]"),
    ],
)
def test_a_bsn_in_a_phrase_is_refused(condition: Condition) -> None:
    with pytest.raises(ApiError) as excinfo:
        check([condition])
    assert excinfo.value.message.startswith("Een BSN bewaren we nooit")


def test_a_masked_phrase_the_rule_already_stores_may_stay() -> None:
    validate(
        "all",
        [Condition("text", "contains_word", "[BSN]")],
        [("add_label", LABEL, None)],
        label_ids={LABEL},
        person_ids=set(),
        kept_values={"[BSN]"},
    )
    stored = Rule(definition=to_json("all", [text_word("111222333")], [("add_label", LABEL, None)]))
    assert definition.phrase_values(stored) == {"[BSN]"}
    assert definition.phrase_values(Rule()) == frozenset()

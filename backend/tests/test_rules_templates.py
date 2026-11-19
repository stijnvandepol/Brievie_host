"""The example rules of the gallery: each one, filled in, is a valid rule that does what
its sentence says.

The checks here need no database (they also run with `pytest --noconftest`); the API
tests live in test_rules_api.py.
"""

import uuid

import pytest

from apps.labels.presets import PRESETS
from apps.rules import definition, templates
from apps.rules.engine import OPERATORS, Condition, DocumentFacts, evaluate
from apps.rules.models import Rule
from apps.rules.schemas import RuleIn, RuleTemplateOut
from apps.rules.templates import RuleTemplate
from apps.workspaces.models import Workspace

LABEL = uuid.uuid4()
PERSON = uuid.uuid4()

# Per template: a letter (text, sender, type) its rule must match once filled in with
# the examples, and one it must leave alone.
LETTERS: dict[str, tuple[tuple[str, str, str], tuple[str, str, str]]] = {
    "label_by_sender": (
        ("Uw nieuwe polis voor 2027.", "Zilveren Kruis Achmea", "policy"),
        ("Wij vergoeden niet alles dat Zilveren Kruis vergoedt.", "Menzis", "policy"),
    ),
    "label_by_word": (
        ("Uw auto moet voor 1 maart naar de APK.", "RDW", "information"),
        ("Uw auto is verzekerd.", "Centraal Beheer", "policy"),
    ),
    "person_by_sender": (
        ("Uw studiefinanciering wordt verhoogd.", "DUO", "decision"),
        ("Uw toeslag wordt verhoogd.", "Belastingdienst", "decision"),
    ),
    "person_by_word": (
        ("Uitnodiging ouderavond Basisschool De Regenboog.", "Gemeente Utrecht", "information"),
        ("Uitnodiging ouderavond Het Kompas.", "Gemeente Utrecht", "information"),
    ),
    "supplier_invoice": (
        ("Factuur 2026-118 voor kantoorartikelen.", "Makro Nederland", "invoice"),
        ("Bedankt voor uw bestelling.", "Makro Nederland", "information"),
    ),
    "municipality_woz": (
        ("Beschikking WOZ-waarde 2027 van uw woning.", "Gemeente Utrecht", "decision"),
        ("De WOZ-waarde bepaalt de hoogte van uw premie.", "Interpolis", "policy"),
    ),
    "reminders": (
        ("U hebt de factuur nog niet betaald.", "Eneco", "demand"),
        ("Uw jaarafrekening.", "Eneco", "bill"),
    ),
}


def all_templates() -> tuple[RuleTemplate, ...]:
    return templates.rule_templates()


def ids(template: RuleTemplate) -> str:
    return template.key


def filled(template: RuleTemplate) -> RuleIn:
    """The template as the editor sends it once every blank holds its example."""
    return RuleIn.model_validate(
        {
            "name": template.name,
            "mode": template.mode,
            "match": template.match,
            "conditions": [
                {"field": c.field, "op": c.op, "value": c.value or c.example}
                for c in template.conditions
            ],
            "actions": [
                {"type": a.type, "label_id": str(LABEL)}
                if a.type == "add_label"
                else {"type": a.type, "person_id": str(PERSON)}
                for a in template.actions
            ],
        }
    )


def facts(letter: tuple[str, str, str]) -> DocumentFacts:
    text, sender, document_type = letter
    return DocumentFacts(
        text=text,
        sender=sender,
        document_type=document_type,
        source="scan",
        label_ids=frozenset(),
        addressee_ids=frozenset(),
    )


@pytest.mark.parametrize("template", all_templates(), ids=ids)
def test_a_filled_in_template_is_a_valid_rule(template: RuleTemplate) -> None:
    data = filled(template)
    conditions = [Condition(c.field, c.op, c.value) for c in data.conditions]
    actions: list[definition.ActionIn] = [(a.type, a.label_id, a.person_id) for a in data.actions]
    definition.validate(data.match, conditions, actions, label_ids={LABEL}, person_ids={PERSON})
    rule = Rule(
        name=data.name,
        mode=data.mode,
        stop=data.stop,
        definition=definition.to_json(data.match, conditions, actions),
    )
    assert data.mode in Rule.Mode.values
    assert definition.to_spec(rule) is not None


@pytest.mark.parametrize("template", all_templates(), ids=ids)
def test_a_template_matches_its_example_letter_and_not_the_other(
    template: RuleTemplate,
) -> None:
    data = filled(template)
    conditions = [Condition(c.field, c.op, c.value) for c in data.conditions]
    actions: list[definition.ActionIn] = [(a.type, a.label_id, a.person_id) for a in data.actions]
    rule = Rule(
        name=data.name,
        mode=data.mode,
        definition=definition.to_json(data.match, conditions, actions),
    )
    spec = definition.to_spec(rule)
    assert spec is not None
    hit, miss = LETTERS[template.key]
    assert evaluate(facts(hit), [spec]).matched_rule_ids == (spec.id,)
    assert evaluate(facts(miss), [spec]).matched_rule_ids == ()


@pytest.mark.parametrize("template", all_templates(), ids=ids)
def test_a_template_proposes_and_says_so(template: RuleTemplate) -> None:
    # A rule from the gallery starts as a proposal the user confirms; its sentence
    # describes that, not an automatic change.
    assert template.mode == Rule.Mode.PROPOSE
    assert template.description.startswith("Stelt ")


@pytest.mark.parametrize("template", all_templates(), ids=ids)
def test_a_template_has_texts_and_known_parts(template: RuleTemplate) -> None:
    assert template.title
    assert template.description
    assert template.name
    assert 1 <= len(template.name) <= 100
    assert template.match in definition.MATCHES
    assert template.kinds
    assert set(template.kinds) <= set(Workspace.Kind.values)
    assert template.conditions
    assert template.actions
    for condition in template.conditions:
        assert condition.op in OPERATORS[condition.field]
        # The user only ever types a word or a name: a label, person or type is chosen.
        if not condition.value:
            assert condition.field in definition.VALUE_LENGTH
            assert condition.example
            assert condition.hint
    for action in template.actions:
        assert action.hint


@pytest.mark.parametrize("template", all_templates(), ids=ids)
def test_a_template_never_names_a_real_label_or_person(template: RuleTemplate) -> None:
    """The user picks the label and the person; a suggested label is a start-set label of
    every kind the template is for, named as the start set names it."""
    for condition in template.conditions:
        assert condition.field not in ("label", "addressee")
    for action in template.actions:
        if not action.label_key:
            assert action.label_name == ""
            continue
        assert action.type == "add_label"
        for kind in template.kinds:
            names = {preset.key: preset.name for preset in PRESETS[kind]}
            assert names.get(action.label_key) == action.label_name


def test_template_keys_are_unique_and_the_gallery_covers_the_basics() -> None:
    found = all_templates()
    keys = [template.key for template in found]
    assert len(keys) == len(set(keys))
    assert set(keys) == set(LETTERS)
    shapes = {
        (
            frozenset(c.field for c in template.conditions),
            frozenset(a.type for a in template.actions),
        )
        for template in found
    }
    for field in ("sender", "text"):
        for action in ("add_label", "add_addressee"):
            assert (frozenset({field}), frozenset({action})) in shapes
    # One combines two conditions that must both hold.
    assert any(t.match == "all" and len(t.conditions) >= 2 for t in found)
    # Something for each kind of workspace.
    for kind in Workspace.Kind.values:
        assert any(kind in template.kinds and len(template.kinds) == 1 for template in found)


@pytest.mark.parametrize("template", all_templates(), ids=ids)
def test_a_template_reads_as_its_api_schema(template: RuleTemplate) -> None:
    out = RuleTemplateOut.model_validate(template)
    assert out.key == template.key
    assert out.kinds == list(template.kinds)
    assert [c.value for c in out.conditions] == [c.value for c in template.conditions]
    assert [a.label_key for a in out.actions] == [a.label_key for a in template.actions]


def test_a_word_template_warns_that_the_word_may_appear_anywhere() -> None:
    for template in all_templates():
        if {c.field for c in template.conditions} == {"text"}:
            assert "overal in de brief" in template.tip, template.key

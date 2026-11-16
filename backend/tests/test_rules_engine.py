import ast
import inspect
from uuid import UUID, uuid4

from apps.rules import engine
from apps.rules.engine import (
    Action,
    Condition,
    DocumentFacts,
    PlannedAction,
    RuleSpec,
    evaluate,
)

ZORG = uuid4()
BANK = uuid4()
MAMA = uuid4()
PAPA = uuid4()


def facts(
    text: str = "",
    sender: str = "",
    *,
    document_type: str = "letter",
    source: str = "scan",
    label_ids: frozenset[UUID] = frozenset(),
    addressee_ids: frozenset[UUID] = frozenset(),
    hoped_label_ids: frozenset[UUID] = frozenset(),
    hoped_addressee_ids: frozenset[UUID] = frozenset(),
) -> DocumentFacts:
    return DocumentFacts(
        text=text,
        sender=sender,
        document_type=document_type,
        source=source,
        label_ids=label_ids,
        addressee_ids=addressee_ids,
        hoped_label_ids=hoped_label_ids,
        hoped_addressee_ids=hoped_addressee_ids,
    )


def rule(
    *conditions: Condition,
    actions: tuple[Action, ...] = (Action("add_label", ZORG),),
    match: str = "all",
    mode: str = "auto",
    stop: bool = False,
    name: str = "Regel",
) -> RuleSpec:
    return RuleSpec(
        id=uuid4(),
        name=name,
        match=match,
        conditions=conditions,
        actions=actions,
        mode=mode,
        stop=stop,
    )


def text_word(value: str) -> Condition:
    return Condition("text", "contains_word", value)


def test_operators_and_actions_table() -> None:
    assert engine.OPERATORS["label"] == frozenset({"has", "has_not"})
    assert engine.OPERATORS["addressee"] == frozenset({"is", "is_empty"})
    assert frozenset({"add_label", "add_addressee"}) == engine.ACTIONS


def test_contains_word_respects_word_boundaries() -> None:
    r = rule(text_word("gas"))
    assert evaluate(facts("Uw rekening voor gasten"), [r]).matched_rule_ids == ()
    assert evaluate(facts("Uw rekening voor gas en stroom"), [r]).matched_rule_ids == (r.id,)


def test_contains_is_a_substring() -> None:
    r = rule(Condition("sender", "contains", "bank"))
    assert evaluate(facts(sender="Rabobank"), [r]).matched_rule_ids == (r.id,)
    word = rule(Condition("sender", "contains_word", "bank"))
    assert evaluate(facts(sender="Rabobank"), [word]).matched_rule_ids == ()


def test_sender_equals_ignores_case_and_accents() -> None:
    r = rule(Condition("sender", "equals", "cafe de hoek"))
    assert evaluate(facts(sender="Café  De Hoek"), [r]).matched_rule_ids == (r.id,)
    assert evaluate(facts(sender="Café De Hoek BV"), [r]).matched_rule_ids == ()


def test_match_all_and_any() -> None:
    conditions = (text_word("APK"), Condition("document_type", "is", "invoice"))
    all_rule = rule(*conditions, match="all")
    any_rule = rule(*conditions, match="any")
    one = facts("Uw APK verloopt", document_type="letter")
    both = facts("Uw APK verloopt", document_type="invoice")
    assert evaluate(one, [all_rule]).matched_rule_ids == ()
    assert evaluate(one, [any_rule]).matched_rule_ids == (any_rule.id,)
    assert evaluate(both, [all_rule]).matched_rule_ids == (all_rule.id,)


def test_not_contains_word_has_not_is_not_is_empty() -> None:
    r = rule(
        Condition("text", "not_contains_word", "aanmaning"),
        Condition("label", "has_not", str(BANK)),
        Condition("document_type", "is_not", "invoice"),
        Condition("addressee", "is_empty", ""),
        Condition("source", "is", "upload"),
    )
    good = facts("Een brief", source="upload")
    assert evaluate(good, [r]).matched_rule_ids == (r.id,)
    for bad in (
        facts("Een aanmaning", source="upload"),
        facts("Een brief", source="upload", label_ids=frozenset({BANK})),
        facts("Een brief", source="upload", document_type="invoice"),
        facts("Een brief", source="upload", addressee_ids=frozenset({PAPA})),
        facts("Een brief", source="scan"),
    ):
        assert evaluate(bad, [r]).matched_rule_ids == (), bad


def test_id_conditions_that_are_not_uuids_do_not_match() -> None:
    for condition in (
        Condition("label", "has", "zorg"),
        Condition("label", "has_not", "zorg"),
        Condition("addressee", "is", "mama"),
    ):
        assert (
            evaluate(facts(label_ids=frozenset({ZORG})), [rule(condition)]).matched_rule_ids == ()
        )


def test_unknown_field_or_operator_does_not_match() -> None:
    for condition in (Condition("colour", "is", "red"), Condition("text", "equals", "brief")):
        assert evaluate(facts("brief"), [rule(condition)]).matched_rule_ids == ()


def test_later_rule_sees_earlier_auto_action() -> None:
    r1 = rule(text_word("huisarts"), actions=(Action("add_label", ZORG),))
    r2 = rule(
        Condition("label", "has", str(ZORG)),
        actions=(Action("add_addressee", MAMA),),
        name="Zorg naar mama",
    )
    result = evaluate(facts("Bericht van de huisarts"), [r1, r2])
    assert result.matched_rule_ids == (r1.id, r2.id)
    assert [(p.action, p.target_id, p.mode) for p in result.planned] == [
        ("add_label", ZORG, "auto"),
        ("add_addressee", MAMA, "auto"),
    ]
    assert result.planned[1].rule_name == "Zorg naar mama"


def test_rule_depending_on_a_proposal_is_a_proposal() -> None:
    r1 = rule(text_word("huisarts"), actions=(Action("add_label", ZORG),), mode="propose")
    r2 = rule(Condition("label", "has", str(ZORG)), actions=(Action("add_addressee", MAMA),))
    result = evaluate(facts("Bericht van de huisarts"), [r1, r2])
    assert [(p.action, p.target_id, p.mode) for p in result.planned] == [
        ("add_label", ZORG, "propose"),
        ("add_addressee", MAMA, "propose"),
    ]


def test_open_suggestion_counts_as_hoped_not_settled() -> None:
    r = rule(Condition("addressee", "is", str(PAPA)), actions=(Action("add_label", BANK),))
    result = evaluate(facts(hoped_addressee_ids=frozenset({PAPA})), [r])
    assert result.matched_rule_ids == (r.id,)
    assert [(p.target_id, p.mode) for p in result.planned] == [(BANK, "propose")]

    settled = evaluate(facts(addressee_ids=frozenset({PAPA})), [r])
    assert [(p.target_id, p.mode) for p in settled.planned] == [(BANK, "auto")]


def test_has_not_on_a_hoped_label_does_not_match_at_all() -> None:
    # A rule matches on the hoped state, so a label that is only proposed already counts
    # as there: "label has_not X" does not hold, not even as a proposal.
    r = rule(Condition("label", "has_not", str(ZORG)), actions=(Action("add_label", BANK),))
    result = evaluate(facts(hoped_label_ids=frozenset({ZORG})), [r])
    assert result.matched_rule_ids == ()
    assert result.planned == ()


def test_stop_ends_the_run_after_own_actions() -> None:
    r1 = rule(
        text_word("brief"),
        actions=(Action("add_label", ZORG), Action("add_addressee", MAMA)),
        stop=True,
    )
    r2 = rule(text_word("brief"), actions=(Action("add_label", BANK),))
    result = evaluate(facts("Een brief"), [r1, r2])
    assert result.matched_rule_ids == (r1.id,)
    assert [p.target_id for p in result.planned] == [ZORG, MAMA]


def test_stop_on_a_rule_that_does_not_match_changes_nothing() -> None:
    r1 = rule(text_word("aanmaning"), stop=True)
    r2 = rule(text_word("brief"), actions=(Action("add_label", BANK),))
    result = evaluate(facts("Een brief"), [r1, r2])
    assert result.matched_rule_ids == (r2.id,)


def test_noop_action_is_not_planned_but_rule_counts_as_matched() -> None:
    r = rule(text_word("brief"), actions=(Action("add_label", ZORG),))
    result = evaluate(facts("Een brief", label_ids=frozenset({ZORG})), [r])
    assert result.matched_rule_ids == (r.id,)
    assert result.planned == ()

    proposer = rule(text_word("brief"), mode="propose")
    hoped = evaluate(facts("Een brief", hoped_label_ids=frozenset({ZORG})), [proposer])
    assert hoped.matched_rule_ids == (proposer.id,)
    assert hoped.planned == ()


def test_same_action_twice_is_planned_once() -> None:
    r1 = rule(text_word("brief"))
    r2 = rule(text_word("brief"))
    result = evaluate(facts("Een brief"), [r1, r2])
    assert result.matched_rule_ids == (r1.id, r2.id)
    assert [p.rule_id for p in result.planned] == [r1.id]


def test_auto_rule_settles_a_proposed_label() -> None:
    proposer = rule(text_word("brief"), mode="propose")
    settler = rule(text_word("brief"), mode="auto")
    result = evaluate(facts("Een brief"), [proposer, settler])
    assert [(p.rule_id, p.mode) for p in result.planned] == [
        (proposer.id, "propose"),
        (settler.id, "auto"),
    ]

    from_suggestion = evaluate(facts("Een brief", hoped_label_ids=frozenset({ZORG})), [settler])
    assert [(p.target_id, p.mode) for p in from_suggestion.planned] == [(ZORG, "auto")]


def test_quote_is_the_matched_words_with_context() -> None:
    text = "Geachte heer,\nHierbij ontvangt u de oproep voor de APK van uw auto."
    r = rule(
        Condition("document_type", "is", "letter"),
        Condition("text", "not_contains_word", "aanmaning"),
        text_word("apk"),
        Condition("sender", "contains", "RDW"),
    )
    [planned] = evaluate(facts(text, "RDW Veendam"), [r]).planned
    assert planned == PlannedAction(
        rule_id=r.id,
        rule_name="Regel",
        action="add_label",
        target_id=ZORG,
        mode="auto",
        quote="Hierbij ontvangt u de oproep voor de APK",
    )


def test_quote_skips_conditions_that_did_not_match() -> None:
    r = rule(text_word("aanmaning"), Condition("sender", "contains", "rdw"), match="any")
    [planned] = evaluate(facts("Een brief", "RDW Veendam"), [r]).planned
    assert planned.quote == "RDW"


def test_quote_is_empty_without_a_text_or_sender_condition() -> None:
    r = rule(Condition("source", "is", "scan"))
    [planned] = evaluate(facts("Een brief"), [r]).planned
    assert planned.quote == ""


def test_engine_imports_no_django() -> None:
    tree = ast.parse(inspect.getsource(engine))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    assert modules
    assert not [m for m in modules if m.split(".")[0] == "django"]

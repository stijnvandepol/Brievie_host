"""Where a rule's words were found: on the sender, in the letterhead or only in running text.

The autopilot applies a rule's label proposal on its own only when it does not rest on a
keyword in the running text (`rests_on_letterhead`).
"""

from uuid import uuid4

from apps.rules.engine import (
    LETTERHEAD_CHARS,
    LETTERHEAD_LINES,
    Action,
    Condition,
    DocumentFacts,
    RuleSpec,
    letterhead,
    phrase_hits,
    rests_on_letterhead,
)

LABEL = uuid4()

HEAD = (
    "Waterschap Voorbeeldmeer\nPostbus 300, 5800 AA Venray\nDe heer J. de Vries\n"
    "Voorbeeldlaan 12\n5445 XX Landhorst\nDatum: 14 november 2026\nAanslag 2027\n"
    "Geachte heer De Vries,\n"
)
BODY = "Dit is de lopende tekst van de brief.\n" * 12


def facts(text: str, sender: str = "") -> DocumentFacts:
    return DocumentFacts(
        text=text,
        sender=sender,
        document_type="letter",
        source="scan",
        label_ids=frozenset(),
        addressee_ids=frozenset(),
    )


def rule(*conditions: Condition, match: str = "all") -> RuleSpec:
    return RuleSpec(
        id=uuid4(),
        name="Regel",
        match=match,
        conditions=conditions,
        actions=(Action("add_label", LABEL),),
        mode="propose",
        stop=False,
    )


def word(value: str) -> Condition:
    return Condition("text", "contains_word", value)


# --- the letterhead ---------------------------------------------------------------------


def test_the_letterhead_ends_at_the_first_blank_line() -> None:
    assert letterhead("\n\n  \nGemeente Venray\nBurgerzaken\n\nGeachte heer,\n") == (
        "Gemeente Venray\nBurgerzaken"
    )


def test_the_letterhead_holds_at_most_eight_lines() -> None:
    lines = [f"regel {n}" for n in range(1, 13)]
    assert LETTERHEAD_LINES == 8
    assert letterhead("\n".join(lines)) == "\n".join(lines[:8])


def test_the_letterhead_holds_at_most_400_characters_and_no_half_word() -> None:
    assert LETTERHEAD_CHARS == 400
    text = "a" * 395 + " Belastingdienstverlening en meer"
    head = letterhead(text)
    assert len(head) <= LETTERHEAD_CHARS
    # The word cut at the limit is left out, so it cannot pass for "Belastingdienst".
    assert head == "a" * 395
    assert letterhead("b" * 399 + " c") == "b" * 399
    assert letterhead("b" * 399 + "c d") == "b" * 399 + "c"
    assert letterhead("b" * 500) == ""


def test_an_empty_text_has_an_empty_letterhead() -> None:
    assert letterhead("") == ""
    assert letterhead("\n \n") == ""


# --- where a condition holds --------------------------------------------------------------


def test_a_word_in_the_letterhead_rests_on_the_letterhead() -> None:
    spec = rule(word("waterschap"))
    found = facts(HEAD + BODY)
    hits = phrase_hits(spec, found)
    assert [(h.on_sender, h.in_letterhead) for h in hits] == [(False, True)]
    assert rests_on_letterhead(spec, found) is True


def test_a_word_only_in_running_text_does_not() -> None:
    spec = rule(word("lopende"))
    found = facts(HEAD + BODY)
    assert [(h.on_sender, h.in_letterhead) for h in phrase_hits(spec, found)] == [(False, False)]
    assert rests_on_letterhead(spec, found) is False


def test_a_word_in_running_text_that_the_sender_holds_rests_on_the_sender() -> None:
    text = "Postbus 300\n" + BODY + "Met vriendelijke groet,\nWaterschap Voorbeeldmeer"
    spec = rule(word("Voorbeeldmeer"))
    assert rests_on_letterhead(spec, facts(text)) is False
    with_sender = facts(text, sender="Waterschap Voorbeeldmeer")
    assert [(h.on_sender, h.in_letterhead) for h in phrase_hits(spec, with_sender)] == [
        (True, False)
    ]
    assert rests_on_letterhead(spec, with_sender) is True


def test_a_sender_condition_rests_on_the_sender() -> None:
    spec = rule(Condition("sender", "contains", "Waterschap"))
    found = facts(BODY, sender="Waterschap Voorbeeldmeer")
    assert [(h.on_sender, h.in_letterhead) for h in phrase_hits(spec, found)] == [(True, False)]
    assert rests_on_letterhead(spec, found) is True


def test_every_condition_that_holds_must_rest_on_the_letterhead() -> None:
    # Both hold; one only in running text: the rule rests on that keyword.
    spec = rule(word("waterschap"), word("lopende"))
    assert rests_on_letterhead(spec, facts(HEAD + BODY)) is False


def test_a_condition_that_does_not_hold_does_not_count() -> None:
    # Match "any": "waterschap" holds in the letterhead, "afvalstoffenheffing" nowhere.
    spec = rule(word("waterschap"), word("afvalstoffenheffing"), match="any")
    assert [h.condition.value for h in phrase_hits(spec, facts(HEAD + BODY))] == ["waterschap"]
    assert rests_on_letterhead(spec, facts(HEAD + BODY)) is True


def test_a_rule_without_a_word_condition_rests_on_no_keyword() -> None:
    spec = rule(Condition("document_type", "is", "letter"))
    assert phrase_hits(spec, facts(HEAD + BODY)) == ()
    assert rests_on_letterhead(spec, facts(HEAD + BODY)) is True


def test_a_negated_word_is_no_hit() -> None:
    spec = rule(Condition("text", "not_contains_word", "reclame"))
    assert phrase_hits(spec, facts(HEAD + BODY)) == ()
    assert rests_on_letterhead(spec, facts(HEAD + BODY)) is True

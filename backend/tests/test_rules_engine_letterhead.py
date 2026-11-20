"""Whether a rule holds on settled facts alone: the letterhead, a confirmed sender and type,
the labels and persons on the letter. The autopilot applies a rule's label proposal on its
own only then (`holds_on_settled`), not on a keyword in the running text or a guess.
"""

from uuid import UUID, uuid4

from apps.rules.engine import (
    LETTERHEAD_CHARS,
    LETTERHEAD_LINES,
    Action,
    Condition,
    DocumentFacts,
    RuleSpec,
    holds_on_settled,
    letterhead,
)

LABEL = uuid4()
PERSON = uuid4()

HEAD = (
    "Waterschap Voorbeeldmeer\nPostbus 300, 5800 AA Venray\nDe heer J. de Vries\n"
    "Voorbeeldlaan 12\n5445 XX Landhorst\nDatum: 14 november 2026\nAanslag 2027\n"
)
SALUTATION = "Geachte heer De Vries,\n"
BODY = "Dit is de lopende tekst van de brief.\n" * 12


def facts(
    text: str,
    sender: str = "",
    *,
    document_type: str = "",
    label_ids: frozenset[UUID] = frozenset(),
    addressee_ids: frozenset[UUID] = frozenset(),
) -> DocumentFacts:
    return DocumentFacts(
        text=text,
        sender=sender,
        document_type=document_type,
        source="scan",
        label_ids=label_ids,
        addressee_ids=addressee_ids,
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


LETTER = HEAD + SALUTATION + BODY

# --- the letterhead ---------------------------------------------------------------------


def test_the_letterhead_ends_at_the_first_blank_line() -> None:
    assert letterhead("\n\n  \nGemeente Venray\nBurgerzaken\n\nOns kenmerk,\n") == (
        "Gemeente Venray\nBurgerzaken"
    )


def test_the_letterhead_ends_at_the_salutation() -> None:
    assert letterhead("Gemeente Venray\nBetreft: uw paspoort\nGeachte heer,\nTekst") == (
        "Gemeente Venray\nBetreft: uw paspoort"
    )
    for salutation in ("Beste Jan,", "Hallo,", "Dear Sir,", "L.S.", "GEACHTE MEVROUW"):
        assert letterhead(f"Afzender\n{salutation}\nTekst") == "Afzender"
    # "Aan" and "Betreft" belong to the head; a word that only starts like one does too.
    assert letterhead("Afzender\nAan de bewoners\nBestemming: Venray") == (
        "Afzender\nAan de bewoners\nBestemming: Venray"
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


# --- holding on settled facts ---------------------------------------------------------------


def test_a_word_in_the_letterhead_holds() -> None:
    assert holds_on_settled(rule(word("waterschap")), facts(LETTER)) is True


def test_a_word_only_in_running_text_does_not() -> None:
    assert holds_on_settled(rule(word("lopende")), facts(LETTER)) is False


def test_a_word_after_the_salutation_is_running_text() -> None:
    assert holds_on_settled(rule(word("Geachte")), facts(LETTER)) is False


def test_a_word_in_running_text_that_a_confirmed_sender_holds_is_still_running_text() -> None:
    # The caller passes the confirmed sender; a text condition is looked for in the head.
    signed = "Postbus 300\n" + BODY + "Met vriendelijke groet,\nWaterschap Voorbeeldmeer"
    found = facts(signed, sender="Waterschap Voorbeeldmeer")
    assert holds_on_settled(rule(word("Voorbeeldmeer")), found) is False


def test_a_sender_condition_holds_on_the_confirmed_sender_or_in_the_letterhead() -> None:
    condition = Condition("sender", "contains", "Waterschap")
    signed = "Postbus 300\n" + BODY + "Waterschap Voorbeeldmeer"
    # Settled facts carry a sender only when a person confirmed it.
    assert holds_on_settled(rule(condition), facts(signed, sender="Waterschap X")) is True
    assert holds_on_settled(rule(condition), facts(signed)) is False
    assert holds_on_settled(rule(condition), facts(LETTER)) is True


def test_a_sender_equals_condition_needs_a_letterhead_line_that_equals_it() -> None:
    condition = Condition("sender", "equals", "Waterschap Voorbeeldmeer")
    assert holds_on_settled(rule(condition), facts(LETTER)) is True
    assert holds_on_settled(rule(Condition("sender", "equals", "Waterschap")), facts(LETTER)) is (
        False
    )


def test_every_condition_must_hold_on_settled_facts() -> None:
    spec = rule(word("waterschap"), word("lopende"))
    assert holds_on_settled(spec, facts(LETTER)) is False


def test_a_condition_that_does_not_hold_does_not_count_in_any() -> None:
    spec = rule(word("waterschap"), word("afvalstoffenheffing"), match="any")
    assert holds_on_settled(spec, facts(LETTER)) is True


def test_a_type_holds_only_when_settled() -> None:
    spec = rule(Condition("document_type", "is", "invoice"))
    assert holds_on_settled(spec, facts(LETTER, document_type="invoice")) is True
    # Not confirmed: the caller passes no type.
    assert holds_on_settled(spec, facts(LETTER)) is False


def test_a_label_or_person_holds_only_when_on_the_letter() -> None:
    other = uuid4()
    has_label = rule(Condition("label", "has", str(other)))
    assert holds_on_settled(has_label, facts(LETTER, label_ids=frozenset({other}))) is True
    hoped = DocumentFacts(
        text=LETTER,
        sender="",
        document_type="",
        source="scan",
        label_ids=frozenset(),
        addressee_ids=frozenset(),
        hoped_label_ids=frozenset({other}),
    )
    assert holds_on_settled(has_label, hoped) is False
    is_person = rule(Condition("addressee", "is", str(PERSON)))
    assert holds_on_settled(is_person, facts(LETTER, addressee_ids=frozenset({PERSON})))
    assert holds_on_settled(is_person, facts(LETTER)) is False


def test_the_source_holds() -> None:
    assert holds_on_settled(rule(Condition("source", "is", "scan")), facts(LETTER)) is True


def test_only_absences_do_not_count() -> None:
    assert holds_on_settled(
        rule(Condition("text", "not_contains_word", "reclame")), facts(LETTER)
    ) is (False)
    assert holds_on_settled(rule(Condition("addressee", "is_empty", "")), facts(LETTER)) is False


def test_a_negated_word_is_looked_for_in_the_whole_letter() -> None:
    spec = rule(word("waterschap"), Condition("text", "not_contains_word", "lopende"))
    assert holds_on_settled(spec, facts(LETTER)) is False
    spec = rule(word("waterschap"), Condition("text", "not_contains_word", "reclame"))
    assert holds_on_settled(spec, facts(LETTER)) is True

from decimal import Decimal
from uuid import uuid4

from apps.people.matching import PRESELECT_FROM, NameMatch, PersonNames, match_addressees

LETTER = (
    "Belastingdienst\nT.a.v. de heer J. de Vries\nDorpsstraat 1\n\nGeachte heer De Vries,\n"
    + "x " * 400
    + "\fPagina 2: Jan de Vries"
)


def person(*names: str) -> PersonNames:
    return PersonNames(person_id=uuid4(), names=names)


def test_address_word_gives_095() -> None:
    p = person("J. de Vries")
    assert match_addressees(LETTER, [p]) == [
        NameMatch(p.person_id, "T.a.v. de heer J. de Vries", Decimal("0.950"))
    ]


def test_address_block_without_word_gives_085() -> None:
    p = person("J. de Vries")
    text = "Belastingdienst\nJ. de Vries\nDorpsstraat 1\n" + "x " * 400
    [match] = match_addressees(text, [p])
    assert match.confidence == Decimal("0.850")
    assert match.quote == "J. de Vries"


def test_elsewhere_gives_060() -> None:
    p = person("Jan de Vries")
    [match] = match_addressees(LETTER, [p])
    assert match.confidence == Decimal("0.600")
    assert match.quote == "Pagina 2: Jan de Vries"


def test_after_600_characters_on_the_first_page_gives_060() -> None:
    p = person("Jan de Vries")
    text = "x " * 400 + "Jan de Vries"
    [match] = match_addressees(text, [p])
    assert match.confidence == Decimal("0.600")


def test_whole_words_only() -> None:
    assert match_addressees("Aan Jan de Vriesema", [person("Jan de Vries")]) == []
    assert match_addressees("Op 3 januari", [person("Jan")]) == []


def test_names_shorter_than_three_are_ignored() -> None:
    assert match_addressees("Aan Jo Smit", [person("Jo")]) == []


def test_case_accents_and_line_breaks_are_ignored() -> None:
    p = person("rene jansen")
    [match] = match_addressees("Beste\nRENÉ\nJANSEN,\nuw polis", [p])
    assert match.person_id == p.person_id
    assert match.confidence == Decimal("0.950")


def test_two_persons_can_match() -> None:
    a, b = person("Jan de Vries"), person("Emma de Vries")
    text = "Familie\nJan de Vries en Emma de Vries"
    assert {m.person_id for m in match_addressees(text, [a, b])} == {a.person_id, b.person_id}


def test_best_match_per_person() -> None:
    # The name occurs on page 2 (0.60) and in the address block (0.85): one match, the best.
    p = person("Jan de Vries", "Jan")
    text = "Brief\nJan de Vries\n" + "x " * 10 + "\fJan de Vries"
    [match] = match_addressees(text, [p])
    assert match.confidence == Decimal("0.850")
    assert match.quote == "Jan de Vries"


def test_longest_name_wins_on_equal_confidence() -> None:
    p = person("Vries", "Jan de Vries")
    [match] = match_addressees("Brief\nJan de Vries\nStraat", [p])
    assert match.quote.endswith("Jan de Vries")


def test_no_persons_or_empty_text() -> None:
    assert match_addressees("Aan Jan de Vries", []) == []
    assert match_addressees("", [person("Jan de Vries")]) == []
    assert match_addressees("Aan Jan de Vries", [person()]) == []


def test_preselect_threshold() -> None:
    assert Decimal("0.85") == PRESELECT_FROM

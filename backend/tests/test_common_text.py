import pytest

from apps.common.text import (
    excerpt,
    find_all_phrases,
    find_phrase,
    has_direction_control,
    is_visible,
    normalise,
    prepare,
)


def test_normalise_folds_case_accents_and_whitespace() -> None:
    assert normalise("  RENÉ\n\tJansen  ") == "rene jansen"


def test_normalise_empty() -> None:
    assert normalise("   \n ") == ""


def test_find_phrase_returns_span_in_original_text() -> None:
    text = "Aan  René   Jansen,\nDorpsstraat 1"
    span = find_phrase(text, "rene jansen", whole_words=True)
    assert span is not None
    assert text[span[0] : span[1]] == "René   Jansen"


def test_find_phrase_whole_words() -> None:
    assert find_phrase("Jan de Vriesema", "Jan de Vries", whole_words=True) is None
    assert find_phrase("januari", "Jan", whole_words=True) is None
    assert find_phrase("Jan de Vriesema", "Jan de Vries", whole_words=False) == (0, 12)


def test_find_phrase_skips_a_partial_hit_and_finds_the_next() -> None:
    text = "januari en Jan"
    assert find_phrase(text, "jan", whole_words=True) == (11, 14)


def test_find_phrase_no_match_or_empty_phrase() -> None:
    assert find_phrase("abc", "xyz", whole_words=True) is None
    assert find_phrase("abc", "  ", whole_words=True) is None


def test_excerpt_cuts_at_a_line_break() -> None:
    text = "Belastingdienst\nT.a.v. de heer J. de Vries\nStraat"
    start = text.index("J. de")
    assert excerpt(text, (start, start + 11)) == "T.a.v. de heer J. de Vries"


def test_excerpt_limits_the_text_before() -> None:
    text = "a" * 100 + " naam"
    start = text.index("naam")
    assert excerpt(text, (start, start + 4), before=10) == "naam"


def test_excerpt_lead_starts_at_a_word_boundary() -> None:
    text = "aaa bbb ccc naam"
    start = text.index("naam")
    assert excerpt(text, (start, start + 4), before=8) == "bbb ccc naam"
    assert excerpt(text, (start, start + 4), before=6) == "ccc naam"


def test_excerpt_collapses_line_breaks_and_whitespace() -> None:
    text = "Brief\nRENÉ\n  JANSEN,\nrest"
    assert excerpt(text, (6, text.index(",")), before=40) == "RENÉ JANSEN"


def test_soft_hyphen_is_ignored() -> None:
    assert find_phrase("Jan\u00adsen", "Jansen", whole_words=True) == (0, 7)


def test_zero_width_space_is_dropped_so_jande_vries_does_not_match_jan_de_vries() -> None:
    assert normalise("Jan\u200bde Vries") == "jande vries"
    assert find_phrase("Jan\u200bde Vries", "Jan de Vries", whole_words=True) is None


def test_span_covers_combining_marks_of_the_last_character() -> None:
    text = "Rene\u0301 Jansen"
    assert find_phrase(text, "rene", whole_words=True) == (0, 5)


def test_a_prepared_text_gives_the_same_spans() -> None:
    text = "Aan  René   Jansen,\nRené Jansen"
    prepared = prepare(text)
    assert prepared.original == text
    assert list(find_all_phrases(prepared, "rene jansen", whole_words=True)) == list(
        find_all_phrases(text, "rene jansen", whole_words=True)
    )
    assert find_phrase(prepared, "Jansen", whole_words=True) == (12, 18)


# A BSN is masked to "[BSN]" before a letter's text is stored, and so is a BSN typed
# into a name or a rule (older rows). The token stands for any number, so it may never
# match: a stored "[BSN]" would otherwise find every letter that held any BSN.
MASKED_LETTER = "Burgerservicenummer: [BSN]\nT.a.v. J. de Vries"


def test_a_phrase_with_the_mask_token_matches_nothing() -> None:
    assert find_phrase(MASKED_LETTER, "[BSN]", whole_words=True) is None
    assert find_phrase(MASKED_LETTER, "[BSN]", whole_words=False) is None
    assert find_phrase(MASKED_LETTER, "[bsn]", whole_words=False) is None
    assert find_phrase(MASKED_LETTER, "Burgerservicenummer: [BSN]", whole_words=True) is None
    assert find_phrase(MASKED_LETTER, "J [BSN]", whole_words=True) is None


def test_the_mask_token_in_a_letter_is_no_text_to_match() -> None:
    # Neither "bsn" nor the brackets around it are words of the letter.
    assert find_phrase("Uw nummer: [BSN]", "bsn", whole_words=True) is None
    assert find_phrase("Uw nummer: [BSN]", "bsn]", whole_words=False) is None
    assert find_phrase("Uw nummer: [BSN] staat", "nummer: staat", whole_words=True) is None
    # The word BSN written in the letter still counts.
    assert find_phrase("Uw BSN: [BSN]", "bsn", whole_words=True) == (3, 6)


def test_text_around_the_mask_token_still_matches() -> None:
    span = find_phrase(MASKED_LETTER, "J. de Vries", whole_words=True)
    assert span is not None
    assert MASKED_LETTER[span[0] : span[1]] == "J. de Vries"
    assert find_phrase(MASKED_LETTER, "burgerservicenummer", whole_words=True) == (0, 19)


@pytest.mark.parametrize(
    "value",
    ["​", "⠀", "ㅤ", " ​‍ ", "‮​", "️", "́"],
)
def test_names_without_a_visible_character(value: str) -> None:
    assert not is_visible(value)


@pytest.mark.parametrize("value", ["Zorg", "📄", "👨‍👩‍👧", "ß", "José"])
def test_visible_names(value: str) -> None:
    assert is_visible(value)
    assert not has_direction_control(value)


def test_direction_controls() -> None:
    # The finding: "TEST ‮​" turns the text after it around in a list.
    assert has_direction_control("TEST ‮​")
    assert has_direction_control("a⁧b")
    assert not has_direction_control("שלום")


@pytest.mark.parametrize(
    "name",
    [
        "محمد علي‏ (2)",  # Arabic, a right-to-left mark
        "דוד כהן‎",  # Hebrew, a left-to-right mark
        "أحمد؜ بن",  # Arabic letter mark
    ],
)
def test_marks_that_keyboards_put_in_real_names_are_allowed(name: str) -> None:
    assert is_visible(name)
    assert not has_direction_control(name)


@pytest.mark.parametrize("char", ["‪", "‫", "‬", "‭", "‮", "⁦", "⁩"])
def test_embeddings_overrides_and_isolates_are_refused(char: str) -> None:
    assert has_direction_control(f"Naam{char}")

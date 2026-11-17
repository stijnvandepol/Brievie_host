from apps.common.text import excerpt, find_all_phrases, find_phrase, normalise, prepare


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

"""Shared text matching: one way of normalising and finding phrases in a letter.

Pure functions, used by name matching and the rules engine. Both sides of a
comparison are normalised the same way (accents removed, case folded, whitespace
collapsed) while spans always refer to the original text.
"""

import unicodedata
from collections.abc import Iterator


def _normalise_with_map(value: str) -> tuple[str, list[int]]:
    """Normalised text and, per normalised character, its index in `value`."""
    chars: list[str] = []
    origin: list[int] = []
    pending_space = False
    for index, original in enumerate(value):
        if original.isspace():
            pending_space = True
            continue
        folded = "".join(
            c for c in unicodedata.normalize("NFKD", original) if not unicodedata.combining(c)
        ).casefold()
        if not folded:
            continue
        if pending_space and chars:
            chars.append(" ")
            origin.append(index - 1 if index > 0 else 0)
        pending_space = False
        for c in folded:
            chars.append(c)
            origin.append(index)
    return "".join(chars), origin


def normalise(value: str) -> str:
    """NFKD, combining marks removed, case folded, whitespace runs to one space, stripped."""
    return _normalise_with_map(value)[0]


def find_all_phrases(haystack: str, phrase: str, *, whole_words: bool) -> Iterator[tuple[int, int]]:
    """Every span in the original `haystack` where `phrase` occurs after normalising both.

    With `whole_words` the characters around a match may not be letters or digits.
    """
    needle = normalise(phrase)
    if not needle:
        return
    text, origin = _normalise_with_map(haystack)
    position = text.find(needle)
    while position != -1:
        end = position + len(needle)
        before_ok = position == 0 or not text[position - 1].isalnum()
        after_ok = end == len(text) or not text[end].isalnum()
        if not whole_words or (before_ok and after_ok):
            yield origin[position], origin[end - 1] + 1
        position = text.find(needle, position + 1)


def find_phrase(haystack: str, phrase: str, *, whole_words: bool) -> tuple[int, int] | None:
    """The span in the original `haystack` of the first occurrence of `phrase`, or None."""
    return next(find_all_phrases(haystack, phrase, whole_words=whole_words), None)


def excerpt(haystack: str, span: tuple[int, int], *, before: int = 40) -> str:
    """The matched text with up to `before` characters before it, cut at a line break."""
    start, end = span
    lead = haystack[max(0, start - before) : start]
    for line_break in ("\n", "\r", "\f"):
        lead = lead.rsplit(line_break, 1)[-1]
    return (lead + haystack[start:end]).strip()

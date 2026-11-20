"""Shared text matching: one way of normalising and finding phrases in a letter.

Pure functions, used by name matching and the rules engine. Both sides of a
comparison are normalised the same way (accents removed, case folded, whitespace
collapsed) while spans always refer to the original text.

The BSN mask token is no text: it stands for any number, so it never matches. In a
letter it becomes one opaque character that no phrase can contain; a phrase that holds
it (a name or rule value masked before a BSN was refused there) matches nothing.
"""

import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass

from apps.common.privacy import MASK

# What the mask token normalises to: a noncharacter, so neither a letter nor a digit
# (a word boundary) and never typed.
_OPAQUE = "\uffff"
_MASK_FOLDED = MASK.casefold()


def _normalise_with_map(value: str) -> tuple[str, list[int]]:
    """Normalised text and, per normalised character, its index in `value`."""
    chars: list[str] = []
    origin: list[int] = []
    pending_space = False
    index = 0
    while index < len(value):
        original = value[index]
        if value[index : index + len(MASK)].casefold() == _MASK_FOLDED:
            if pending_space and chars:
                chars.append(" ")
                origin.append(index - 1)
            pending_space = False
            chars.append(_OPAQUE)
            origin.append(index + len(MASK) - 1)
            index += len(MASK)
            continue
        index += 1
        if original.isspace():
            pending_space = True
            continue
        folded = "".join(
            c
            for c in unicodedata.normalize("NFKD", original)
            if not unicodedata.combining(c) and unicodedata.category(c) != "Cf"
        ).casefold()
        if not folded:
            continue
        if pending_space and chars:
            chars.append(" ")
            origin.append(index - 2 if index > 1 else 0)
        pending_space = False
        for c in folded:
            chars.append(c)
            origin.append(index - 1)
    return "".join(chars), origin


# Characters that draw nothing although Unicode files them as letters or symbols.
_BLANK_LOOKALIKES = frozenset("\u115f\u1160\u2800\u3164\uffa0")
# Categories that draw nothing on their own: controls, format characters (zero width,
# joiners, direction marks), separators, combining marks without a base, unassigned.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cn", "Cs", "Zs", "Zl", "Zp", "Mn", "Me"})
# Embeddings, overrides and isolates turn the text after them around (U+202E in a
# label name reverses the list it stands in). The plain marks (LRM, RLM, ALM) are not
# here: Hebrew and Arabic keyboards put them in real names, and they reorder nothing
# outside the name.
_DIRECTION_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def is_visible(value: str) -> bool:
    """Whether `value` has at least one character that draws something."""
    return any(
        char not in _BLANK_LOOKALIKES and unicodedata.category(char) not in _INVISIBLE_CATEGORIES
        for char in value
    )


def has_direction_control(value: str) -> bool:
    """Whether `value` holds a character that changes the direction of the text."""
    return any(char in _DIRECTION_CONTROLS for char in value)


def searchable(phrase: str) -> str:
    """`phrase` normalised to search for; empty (matches nothing) when it holds the mask."""
    needle = normalise(phrase)
    return "" if _OPAQUE in needle else needle


def normalise(value: str) -> str:
    """NFKD, combining marks and format characters (Cf) removed, case folded, whitespace
    runs to one space, stripped."""
    return _normalise_with_map(value)[0]


@dataclass(frozen=True)
class Prepared:
    """A text normalised once, to search many phrases in (normalising a long letter is
    the expensive part of a search)."""

    original: str
    normalised: str
    # Per normalised character, its index in `original`.
    origin: tuple[int, ...]


def prepare(text: str) -> Prepared:
    normalised, origin = _normalise_with_map(text)
    return Prepared(text, normalised, tuple(origin))


def find_all_phrases(
    haystack: str | Prepared, phrase: str, *, whole_words: bool
) -> Iterator[tuple[int, int]]:
    """Every span in the original `haystack` where `phrase` occurs after normalising both.

    With `whole_words` the characters around a match may not be letters or digits.
    """
    needle = searchable(phrase)
    if not needle:
        return
    prepared = haystack if isinstance(haystack, Prepared) else prepare(haystack)
    original, text, origin = prepared.original, prepared.normalised, prepared.origin
    position = text.find(needle)
    while position != -1:
        end = position + len(needle)
        before_ok = position == 0 or not text[position - 1].isalnum()
        after_ok = end == len(text) or not text[end].isalnum()
        if not whole_words or (before_ok and after_ok):
            span_end = origin[end - 1] + 1
            # Combining marks of the last character belong to the match.
            while span_end < len(original) and unicodedata.combining(original[span_end]):
                span_end += 1
            yield origin[position], span_end
        position = text.find(needle, position + 1)


def find_phrase(
    haystack: str | Prepared, phrase: str, *, whole_words: bool
) -> tuple[int, int] | None:
    """The span in the original `haystack` of the first occurrence of `phrase`, or None."""
    return next(find_all_phrases(haystack, phrase, whole_words=whole_words), None)


def excerpt(haystack: str, span: tuple[int, int], *, before: int = 40) -> str:
    """The matched text with up to `before` characters before it, on one line.

    The lead is cut at a line break and starts at a word boundary; whitespace runs
    become single spaces.
    """
    start, end = span
    window_start = max(0, start - before)
    lead = haystack[window_start:start]
    for line_break in ("\n", "\r", "\f"):
        lead = lead.rsplit(line_break, 1)[-1]
    cut_mid_word = (
        len(lead) == start - window_start
        and window_start > 0
        and lead[:1].strip() != ""
        and not haystack[window_start - 1].isspace()
    )
    if cut_mid_word:
        _, _, lead = lead.partition(" ") if " " in lead else ("", "", "")
    return " ".join((lead + haystack[start:end]).split())
